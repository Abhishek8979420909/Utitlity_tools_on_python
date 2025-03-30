from flask import Flask, render_template, request, redirect, url_for, send_file, flash
import os
import shutil
from PIL import Image
from werkzeug.utils import secure_filename
import io
import traceback
import subprocess
import time
import psutil
import hashlib
from collections import defaultdict

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Required for flash messages

# Global dictionary to store mappings
file_type_mapping = {}
base_path = ""  # Initialize base path
duplicate_extensions = set()  # Store extensions to check for duplicates

# Configuration for Image to PDF tool
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'pdf'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create uploads folder if it doesn't exist
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def find_locking_process(file_path):
    """Find which process is locking a file."""
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            for item in proc.open_files():
                if item.path == file_path:
                    return proc.info
        except Exception:
            pass
    return None

def safe_remove_file(filepath, max_retries=3):
    """Safely remove a file with retries."""
    for attempt in range(max_retries):
        try:
            # Check if file is locked
            locking_process = find_locking_process(filepath)
            if locking_process:
                print(f"File {filepath} is locked by process {locking_process}")
                time.sleep(1)  # Wait for 1 second
                continue

            # Try to remove the file
            os.remove(filepath)
            return True
        except PermissionError:
            print(f"Permission denied for {filepath}")
            time.sleep(1)
        except FileNotFoundError:
            print(f"File {filepath} not found")
            return True
        except Exception as e:
            print(f"Error removing {filepath}: {e}")
            time.sleep(1)
    return False

def calculate_file_hash(filepath):
    """Calculate MD5 hash of a file."""
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def find_duplicates(directory, extensions):
    """Find duplicate files in the given directory for specified extensions."""
    hash_dict = defaultdict(list)
    
    # Walk through the directory
    for root, _, files in os.walk(directory):
        for filename in files:
            # Check if file extension is in the specified extensions
            if any(filename.lower().endswith(ext) for ext in extensions):
                filepath = os.path.join(root, filename)
                try:
                    file_hash = calculate_file_hash(filepath)
                    hash_dict[file_hash].append(filepath)
                except Exception as e:
                    print(f"Error processing {filepath}: {str(e)}")
    
    # Return only the duplicates (files with same hash)
    return {k: v for k, v in hash_dict.items() if len(v) > 1}

@app.route('/')
def index():
    """Render the home page with all tools."""
    return render_template('index.html')

@app.route('/file-organizer')
def file_organizer():
    """Render the file organizer tool page."""
    return render_template('file_organizer.html', mappings=file_type_mapping)

@app.route('/image-to-pdf')
def image_to_pdf():
    """Render the Image to PDF converter page."""
    return render_template('image_to_pdf.html')

@app.route('/pdf-compressor')
def pdf_compressor():
    """Render the PDF compressor page."""
    return render_template('pdf_compressor.html')

@app.route('/compress-pdf', methods=['POST'])
def compress_pdf_route():
    """Handle PDF compression."""
    try:
        if 'pdf_file' not in request.files:
            return render_template('pdf_compressor.html', error="Please select a PDF file.")
        
        file = request.files['pdf_file']
        if file.filename == '':
            return render_template('pdf_compressor.html', error="No file selected.")

        if not file.filename.lower().endswith('.pdf'):
            return render_template('pdf_compressor.html', error="Please select a PDF file.")

        # Get compression settings
        quality = request.form.get('quality', 'screen')
        output_name = request.form.get('output_name', 'compressed')

        # Save uploaded file
        input_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{output_name}.pdf")
        
        try:
            file.save(input_path)
        except Exception as e:
            print(f"Error saving file: {str(e)}")
            return render_template('pdf_compressor.html', error="Error saving the uploaded file. Please try again.")

        # Check if Ghostscript is installed
        try:
            subprocess.run(['gs', '--version'], capture_output=True, check=True)
        except FileNotFoundError:
            return render_template('pdf_compressor.html', error="Ghostscript is not installed. Please install it first.")
        except subprocess.CalledProcessError:
            return render_template('pdf_compressor.html', error="Error checking Ghostscript installation.")

        # Compress PDF
        try:
            if compress_pdf(input_path, output_path, quality):
                # Clean up input file
                safe_remove_file(input_path)
                
                # Send compressed file
                return send_file(
                    output_path,
                    mimetype='application/pdf',
                    as_attachment=True,
                    download_name=f"{output_name}.pdf"
                )
            else:
                # Clean up both files
                safe_remove_file(input_path)
                safe_remove_file(output_path)
                return render_template('pdf_compressor.html', error="Error compressing PDF. Please try again.")
        except Exception as e:
            print(f"Error during compression: {str(e)}")
            # Clean up both files
            safe_remove_file(input_path)
            safe_remove_file(output_path)
            return render_template('pdf_compressor.html', error="Error during PDF compression. Please try again.")

    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        print(traceback.format_exc())
        return render_template('pdf_compressor.html', error="An unexpected error occurred. Please try again.")

@app.route('/upload-images', methods=['POST'])
def upload_images():
    """Handle image uploads and convert to PDF."""
    try:
        if 'images' not in request.files:
            return render_template('image_to_pdf.html', error="Please select at least one image.")
        
        files = request.files.getlist('images')
        if not files or files[0].filename == '':
            return render_template('image_to_pdf.html', error="Please select at least one image.")

        # Get PDF settings from form
        pdf_name = request.form.get('pdf_name', 'converted')
        
        # Process each image
        images = []
        temp_files = []
        error_files = []
        
        for file in files:
            if file and allowed_file(file.filename):
                try:
                    # Save file temporarily
                    filename = secure_filename(file.filename)
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(filepath)
                    temp_files.append(filepath)
                    
                    # Read and process image
                    with Image.open(filepath) as img:
                        # Convert to RGB if necessary
                        if img.mode != 'RGB':
                            img = img.convert('RGB')
                        
                        # Create a copy of the image to avoid file locking issues
                        img_copy = img.copy()
                        images.append(img_copy)
                    
                except Exception as e:
                    print(f"Error processing image {file.filename}: {str(e)}")
                    error_files.append(file.filename)
                    continue

        if not images:
            # Clean up any temporary files
            for filepath in temp_files:
                safe_remove_file(filepath)
            return render_template('image_to_pdf.html', error="No valid images to convert. Please check your image files.")

        # Create PDF
        output = io.BytesIO()
        try:
            if len(images) == 1:
                images[0].save(output, format='PDF')
            else:
                images[0].save(output, format='PDF', save_all=True, append_images=images[1:])
            
            output.seek(0)
            
            # Clean up temporary files
            for filepath in temp_files:
                safe_remove_file(filepath)
            
            return send_file(
                output,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=f"{pdf_name}.pdf"
            )
        except Exception as e:
            print(f"Error creating PDF: {str(e)}")
            # Clean up temporary files
            for filepath in temp_files:
                safe_remove_file(filepath)
            return render_template('image_to_pdf.html', error="Error creating PDF. Please try again.")

    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        print(traceback.format_exc())
        return render_template('image_to_pdf.html', error="An unexpected error occurred. Please try again.")

@app.route('/set_directory', methods=['POST'])
def set_directory():
    """Set the base directory."""
    global base_path
    base_path = request.form.get('dirpath')

    # Check if the path is valid
    if not os.path.exists(base_path):
        return "Error: Directory does not exist.", 400

    return redirect(url_for('file_organizer'))

@app.route('/add_mapping', methods=['POST'])
def add_mapping():
    """Add folder-extension mapping."""
    folder_name = request.form.get('folder_name').strip()
    extensions = request.form.get('extensions').strip().split(',')

    # Filter out empty extensions
    extensions = [ext.strip().lower() for ext in extensions if ext.strip()]

    if folder_name and extensions:
        file_type_mapping[folder_name] = extensions

    return redirect(url_for('file_organizer'))

@app.route('/delete_mapping/<folder_name>', methods=['POST'])
def delete_mapping(folder_name):
    """Delete a mapping."""
    if folder_name in file_type_mapping:
        del file_type_mapping[folder_name]
    return redirect(url_for('file_organizer'))

@app.route('/organize', methods=['POST'])
def organize_files():
    """Organize files based on mappings."""
    global base_path
    if not base_path or not os.path.exists(base_path):
        return "Error: Base directory is not set or does not exist.", 400

    # Create directories
    for folder in file_type_mapping.keys():
        dir_path = os.path.join(base_path, folder)
        if not os.path.exists(dir_path):
            os.mkdir(dir_path)

    # Organize files
    files = [f for f in os.listdir(base_path) if os.path.isfile(os.path.join(base_path, f))]

    for file in files:
        file_ext = file.split('.')[-1].lower() if '.' in file else ''
        for folder, extensions in file_type_mapping.items():
            if file_ext in extensions:
                shutil.move(
                    os.path.join(base_path, file),
                    os.path.join(base_path, folder, file)
                )
                break

    return "Files organized successfully!"

def compress_pdf(input_file, output_file, quality='screen'):
    """Compress a PDF using Ghostscript."""
    try:
        command = [
            'gs',
            '-sDEVICE=pdfwrite',
            f'-dPDFSETTINGS=/{quality}',
            '-dCompatibilityLevel=1.4',
            '-dNOPAUSE',
            '-dBATCH',
            f'-sOutputFile={output_file}',
            input_file
        ]
        subprocess.run(command, check=True)
        return True
    except Exception as e:
        print(f"Error compressing PDF: {e}")
        return False

@app.route('/duplicate-remover')
def duplicate_remover():
    """Render the duplicate file remover page."""
    return render_template('duplicate_remover.html', duplicates=None)

@app.route('/find-duplicates', methods=['POST'])
def find_duplicates_route():
    """Find duplicate files based on specified extensions."""
    global duplicate_extensions
    
    # Get extensions from form
    extensions = request.form.get('extensions', '').strip().split(',')
    extensions = [ext.strip().lower() for ext in extensions if ext.strip()]
    
    if not extensions:
        return render_template('duplicate_remover.html', error="Please enter at least one file extension.")
    
    # Get directory path
    directory = request.form.get('directory', '').strip()
    if not directory or not os.path.exists(directory):
        return render_template('duplicate_remover.html', error="Please enter a valid directory path.")
    
    # Store extensions for later use
    duplicate_extensions = extensions
    
    # Find duplicates
    duplicates = find_duplicates(directory, extensions)
    
    if not duplicates:
        return render_template('duplicate_remover.html', message="No duplicate files found.")
    
    return render_template('duplicate_remover.html', duplicates=duplicates)

@app.route('/remove-duplicates', methods=['POST'])
def remove_duplicates():
    """Remove duplicate files, keeping only the first occurrence."""
    try:
        directory = request.form.get('directory', '').strip()
        if not directory or not os.path.exists(directory):
            return render_template('duplicate_remover.html', error="Invalid directory path.")
        
        # Find duplicates again
        duplicates = find_duplicates(directory, duplicate_extensions)
        
        if not duplicates:
            return render_template('duplicate_remover.html', message="No duplicate files found.")
        
        # Remove duplicates, keeping the first file
        removed_count = 0
        for hash_value, filepaths in duplicates.items():
            # Keep the first file, remove the rest
            for filepath in filepaths[1:]:
                try:
                    safe_remove_file(filepath)
                    removed_count += 1
                except Exception as e:
                    print(f"Error removing {filepath}: {str(e)}")
        
        return render_template('duplicate_remover.html', 
                             message=f"Successfully removed {removed_count} duplicate files.")
    
    except Exception as e:
        print(f"Error removing duplicates: {str(e)}")
        return render_template('duplicate_remover.html', error="Error removing duplicate files.")

if __name__ == '__main__':
    app.run(debug=True)
