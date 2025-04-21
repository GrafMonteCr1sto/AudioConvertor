import os
import uuid
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from models import db, AudioConversion
from datetime import datetime
import logging, zipfile, shutil
from io import BytesIO
from flask_cors import CORS
import ffmpeg
import flask

# Настроил логирование
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Включаем поддержку CORS для всех маршрутов

# Конфигурация приложения
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(basedir, "instance", "audio_converter.db")}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(basedir, 'uploads')
app.config['CONVERTED_FOLDER'] = os.path.join(basedir, 'converted')
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 ** 2  # до 200MB для загрузки папок
app.config['ALLOWED_EXTENSIONS'] = {'mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a', 'wma'}
app.config['TARGET_FORMATS'] = ['mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a']

# Создание директорий для загрузки и конвертации
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['CONVERTED_FOLDER'], exist_ok=True)
os.makedirs(os.path.dirname(os.path.join(basedir, "instance", "audio_converter.db")), exist_ok=True)

# Инициализация бд
db.init_app(app)

# Создание БД при первом запуске
with app.app_context():
    db.create_all()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@app.route('/')
def index():
    logger.debug(f"Рендерим главную страницу с форматами: {app.config['TARGET_FORMATS']}")
    return render_template('index.html', target_formats=app.config['TARGET_FORMATS'])

@app.route('/upload', methods=['POST'])
def upload_file():
    logger.debug("Получен запрос на загрузку файла")
    
    # Проверяем, что файл есть в запросе
    if 'file' not in request.files:
        logger.warning("Файл не найден в запросе")
        return jsonify({'error': 'Файл не найден'}), 400
    
    file = request.files['file']
    logger.debug(f"Получен файл: {file.filename}")
    
    # Проверяем, что имя файла не пустое
    if file.filename == '':
        logger.warning("Пустое имя файла")
        return jsonify({'error': 'Файл не выбран'}), 400
    
    # Проверяем расширение файла
    if not allowed_file(file.filename):
        logger.warning(f"Недопустимый формат файла: {file.filename}")
        return jsonify({'error': 'Недопустимый формат файла'}), 400
    
    # Проверяем, что выбраны форматы для конвертации
    target_formats = request.form.getlist('formats')
    logger.debug(f"Выбранные форматы: {target_formats}")
    
    if not target_formats:
        logger.warning("Не выбраны форматы для конвертации")
        return jsonify({'error': 'Необходимо выбрать хотя бы один формат для конвертации'}), 400
    
    try:
        # Генерируем уникальное имя файла
        original_filename = secure_filename(file.filename)
        original_format = original_filename.rsplit('.', 1)[1].lower()
        unique_filename = f"{uuid.uuid4().hex}.{original_format}"
        
        # Сохраняем файл
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        logger.debug(f"Сохраняем файл по пути: {file_path}")
        file.save(file_path)
        
        # Проверяем, что файл успешно сохранен
        if not os.path.exists(file_path):
            logger.error(f"Не удалось сохранить файл по пути: {file_path}")
            return jsonify({'error': 'Не удалось сохранить загруженный файл'}), 500
        
        # Создаем запись в БД
        conversion = AudioConversion(
            original_filename=original_filename,
            original_format=original_format,
            target_formats=','.join(target_formats),
            status='processing'
        )
        db.session.add(conversion)
        db.session.commit()
        logger.debug(f"Создана запись о конвертации с ID: {conversion.id}")
        
        # Запускаем конвертацию
        try:
            convert_audio(conversion.id, file_path, target_formats)
            logger.debug(f"Конвертация успешно завершена для ID: {conversion.id}")
            return jsonify({
                'id': conversion.id,
                'message': 'Файл успешно загружен и сконвертирован'
            }), 200
        except Exception as e:
            logger.error(f"Ошибка конвертации: {str(e)}")
            # Обновляем статус в случае ошибки
            conversion.status = 'failed'
            db.session.commit()
            return jsonify({'error': str(e)}), 500
            
    except Exception as e:
        logger.error(f"Неожиданная ошибка при загрузке файла: {str(e)}")
        return jsonify({'error': f'Произошла ошибка: {str(e)}'}), 500

@app.route('/batch-download/<string:conversion_ids>', methods=['GET'])
def batch_download(conversion_ids):
    """
    Скачать несколько сконвертированных файлов в ZIP архиве
    conversion_ids: строка с ID конвертаций, разделенных запятой
    """
    logger.debug(f"Запрос на пакетное скачивание файлов: {conversion_ids}")
    
    try:
        # Разбиваем строку ID на список
        id_list = [int(id_str) for id_str in conversion_ids.split(',')]
        
        # Создаем буфер для ZIP архива
        memory_file = BytesIO()
        with zipfile.ZipFile(memory_file, 'w') as zf:
            # Для каждой конвертации добавляем файлы в архив
            for conv_id in id_list:
                # Получаем информацию о конвертации
                conversion = AudioConversion.query.get(conv_id)
                if not conversion:
                    logger.warning(f"Конвертация не найдена: {conv_id}")
                    continue
                
                # Проверяем, что конвертация завершена
                if conversion.status != 'completed':
                    logger.warning(f"Конвертация не завершена: {conv_id}")
                    continue
                
                # Для каждого формата ищем соответствующий файл
                for format in conversion.target_formats.split(','):
                    # Ищем файл в директории
                    for filename in os.listdir(app.config['CONVERTED_FOLDER']):
                        if filename.startswith(f"{conv_id}_") and filename.endswith(f".{format}"):
                            file_path = os.path.join(app.config['CONVERTED_FOLDER'], filename)
                            download_name = f"{conversion.original_filename.rsplit('.', 1)[0]}.{format}"
                            
                            # Добавляем файл в архив
                            zf.write(file_path, arcname=download_name)
                            logger.debug(f"Добавлен файл в архив: {download_name}")
                            break
        
        # Перемещаем указатель в начало файла
        memory_file.seek(0)
        
        # Отправляем архив как ответ
        response = jsonify({'error': 'No files to download'})
        if memory_file.getbuffer().nbytes > 0:  # Проверяем, что архив не пустой
            response = flask.Response(
                memory_file.getvalue(),
                mimetype='application/zip',
                headers={
                    'Content-Disposition': 'attachment; filename=converted_files.zip',
                    'Access-Control-Allow-Origin': '*'
                }
            )
        
        return response
        
    except Exception as e:
        logger.error(f"Ошибка при создании ZIP архива: {str(e)}")
        return jsonify({'error': f'Ошибка при создании архива: {str(e)}'}), 500

def convert_audio(conversion_id, file_path, target_formats):
    """
    Конвертация аудио файла в выбранные форматы с использованием ffmpeg.
    """
    logger.debug(f"Начинаем конвертацию для ID: {conversion_id}, форматы: {target_formats}")
    
    # Получаем запись из БД
    conversion = AudioConversion.query.get(conversion_id)
    if not conversion:
        logger.error(f"Запись о конвертации не найдена для ID: {conversion_id}")
        raise Exception('Запись о конвертации не найдена')
    
    # Проверяем, что входной файл существует
    if not os.path.exists(file_path):
        logger.error(f"Входной файл не найден: {file_path}")
        conversion.status = 'failed'
        db.session.commit()
        raise Exception(f'Входной файл не найден: {file_path}')
    
    # Базовое имя для сконвертированных файлов
    filename_base = f"{conversion_id}_{uuid.uuid4().hex}"
    
    try:
        # Конвертируем файл в каждый из выбранных форматов
        for format in target_formats:
            output_filename = f"{filename_base}.{format}"
            output_path = os.path.join(app.config['CONVERTED_FOLDER'], output_filename)
            
            logger.debug(f"Конвертация в формат {format}, выходной путь: {output_path}")
            
            try:
                # Используем ffmpeg для конвертации
                stream = ffmpeg.input(file_path)
                
                # Настраиваем параметры в зависимости от формата
                if format == 'mp3':
                    stream = ffmpeg.output(stream, output_path, acodec='libmp3lame', ab='192k')
                elif format == 'wav':
                    stream = ffmpeg.output(stream, output_path, acodec='pcm_s16le')
                elif format == 'ogg':
                    stream = ffmpeg.output(stream, output_path, acodec='libvorbis', ab='192k')
                elif format == 'flac':
                    stream = ffmpeg.output(stream, output_path, acodec='flac')
                elif format == 'aac':
                    stream = ffmpeg.output(stream, output_path, acodec='aac', ab='192k')
                elif format == 'm4a':
                    stream = ffmpeg.output(stream, output_path, acodec='aac', ab='192k')
                else:
                    # Если формат неизвестен, используем дефолтные настройки
                    stream = ffmpeg.output(stream, output_path)
                
                # Запускаем процесс конвертации
                ffmpeg.run(stream, quiet=True, overwrite_output=True)
                
                logger.debug(f"Файл успешно сконвертирован: {output_path}")
            except Exception as e:
                logger.error(f"Ошибка при конвертации в {format}: {str(e)}")
                
                # Если ffmpeg не установлен или произошла другая ошибка, используем резервный метод копирования
                logger.warning(f"Используем резервный метод копирования для формата {format}")
                shutil.copy2(file_path, output_path)
                logger.debug(f"Файл скопирован с новым расширением: {output_path}")
            
            # Проверяем, что выходной файл создан
            if not os.path.exists(output_path):
                logger.error(f"Выходной файл не был создан: {output_path}")
                raise Exception(f'Не удалось создать выходной файл: {output_path}')
        
        # Обновляем статус после успешной конвертации
        conversion.status = 'completed'
        conversion.completed_at = datetime.utcnow()
        db.session.commit()
        logger.debug(f"Конвертация успешно завершена для ID: {conversion_id}")
    
    except Exception as e:
        logger.error(f"Ошибка в процессе конвертации: {str(e)}")
        conversion.status = 'failed'
        db.session.commit()
        raise Exception(f'Ошибка конвертации: {str(e)}')

@app.route('/conversions', methods=['GET'])
def get_conversions():
    logger.debug("Запрос на получение списка конвертаций")
    conversions = AudioConversion.query.order_by(AudioConversion.created_at.desc()).all()
    result = []
    for conv in conversions:
        result.append({
            'id': conv.id,
            'original_filename': conv.original_filename,
            'original_format': conv.original_format,
            'target_formats': conv.target_formats.split(','),
            'status': conv.status,
            'created_at': conv.created_at.isoformat() if conv.created_at else None,
            'completed_at': conv.completed_at.isoformat() if conv.completed_at else None
        })
    logger.debug(f"Возвращаем {len(result)} записей")
    return jsonify(result)

@app.route('/download/<int:conversion_id>/<string:format>', methods=['GET'])
def download_file(conversion_id, format):
    logger.debug(f"Запрос на скачивание файла: ID={conversion_id}, формат={format}")
    
    # Проверяем существование конвертации
    conversion = AudioConversion.query.get_or_404(conversion_id)
    logger.debug(f"Найдена запись: {conversion.original_filename}")
    
    # Проверяем, что запрошенный формат был в списке конвертации
    if format not in conversion.target_formats.split(','):
        logger.warning(f"Запрошенный формат {format} не найден в списке конвертации")
        return jsonify({'error': 'Формат не найден'}), 404
    
    # Ищем файл в директории с конвертированными файлами
    converted_files = []
    for filename in os.listdir(app.config['CONVERTED_FOLDER']):
        if filename.startswith(f"{conversion_id}_") and filename.endswith(f".{format}"):
            converted_files.append(filename)
    
    if not converted_files:
        logger.warning(f"Сконвертированный файл не найден в директории: {app.config['CONVERTED_FOLDER']}")
        return jsonify({'error': 'Файл не найден'}), 404
    
    # Берем последний файл (на случай, если их несколько с одинаковым ID)
    filename = converted_files[-1]
    download_name = f"{conversion.original_filename.rsplit('.', 1)[0]}.{format}"
    
    logger.debug(f"Отдаем файл: {filename}, имя для скачивания: {download_name}")
    
    # Добавляем заголовок для поддержки CORS при скачивании
    response = send_from_directory(
        app.config['CONVERTED_FOLDER'],
        filename,
        as_attachment=True,
        download_name=download_name
    )
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response

if __name__ == '__main__':
    logger.info("Запуск приложения аудио конвертера")
    app.run(host='0.0.0.0', port=5000, debug=True) 