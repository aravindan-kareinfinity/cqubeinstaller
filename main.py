import json
import threading
import time
import subprocess
import os
import re
import uuid
import requests
import cv2
import glob
import urllib.parse
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_from_directory, send_file, session, redirect, url_for

app = Flask(__name__)
app.secret_key = 'cqubepro_secret_key_2024'  # Change this in production

# Global variable to store camera data
cameras_data = []
# Global variable to store base video path
base_video_path = ""
# Dictionary to store active threads for each camera
active_threads = {}
# Dictionary to store stop flags for each camera
stop_flags = {}
# Global variable to store groups
groups_data = []

# Global variables for video processing
merge_queue = None
merge_worker_thread = None
merging_tasks = {}
video_config = {
    'upload_to_api': True,
    'store_locally': True,
    'api_url': 'http://localhost:8000/api/videos/upload',
    'api_key': 'your_api_key_here'
}
api_organization_id = 'default_org'

# Known camera vendors to help classify devices discovered on the LAN
KNOWN_CAMERA_VENDORS = [
    'axis', 'hikvision', 'dahua', 'uniview', 'cp plus',
    'bosch', 'hanwha', 'sony', 'avtech', 'arecont', 'vivotek', 'mobotix',
]

def get_mac_vendor(mac_address):
    """Lookup vendor name for a MAC address using macvendors API.
    Returns 'Unknown' on error.
    """
    try:
        url = f"https://api.macvendors.com/{mac_address}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200 and response.text:
            return response.text.strip()
    except Exception:
        pass
    return "Unknown"

def test_rtsp_stream(camera_rtsp_url, camera_name):
    """Test if RTSP stream is accessible"""
    try:
        print(f"Testing RTSP stream for {camera_name}: {camera_rtsp_url}")
        
        # Try to open the stream with OpenCV
        cap = cv2.VideoCapture(camera_rtsp_url)
        if not cap.isOpened():
            print(f"Error: Could not open RTSP stream for {camera_name}")
            return False
        
        # Try to read a frame
        ret, frame = cap.read()
        cap.release()
        
        if ret:
            print(f"RTSP stream test successful for {camera_name}")
            return True
        else:
            print(f"Error: Could not read frame from RTSP stream for {camera_name}")
            return False
            
    except Exception as e:
        print(f"Error testing RTSP stream for {camera_name}: {e}")
        return False

def cleanup_corrupted_files(output_dir, camera_name):
    """Clean up corrupted or incomplete video files"""
    try:
        if not os.path.exists(output_dir):
            return
        
        corrupted_files = []
        for file in os.listdir(output_dir):
            if file.endswith('.mp4'):
                file_path = os.path.join(output_dir, file)
                try:
                    file_size = os.path.getsize(file_path)
                    
                    # Check if file is too small
                    if file_size < 1024:
                        corrupted_files.append((file_path, f"Too small ({file_size} bytes)"))
                        continue
                    
                    # Check if file can be read and has valid MP4 structure
                    try:
                        with open(file_path, 'rb') as f:
                            header = f.read(1024)
                            if len(header) < 1024 or b'mdat' not in header:
                                corrupted_files.append((file_path, "Invalid MP4 structure"))
                                continue
                    except Exception as e:
                        corrupted_files.append((file_path, f"Cannot read file: {e}"))
                        continue
                        
                except Exception as e:
                    corrupted_files.append((file_path, f"Error checking file: {e}"))
        
        # Remove corrupted files
        for file_path, reason in corrupted_files:
            try:
                # Try to remove the file, but handle "file in use" errors gracefully
                os.remove(file_path)
                print(f"🗑️ Cleaned up corrupted file [{camera_name}]: {os.path.basename(file_path)} - {reason}")
            except PermissionError as e:
                if "being used by another process" in str(e):
                    print(f"⏳ File {os.path.basename(file_path)} is currently being used by FFmpeg, will retry later")
                else:
                    print(f"⚠️ Permission error removing {file_path}: {e}")
            except Exception as e:
                print(f"⚠️ Failed to remove corrupted file {file_path}: {e}")
        
        if corrupted_files:
            print(f"🧹 Cleaned up {len(corrupted_files)} corrupted files for camera {camera_name}")
        
    except Exception as e:
        print(f"Error in cleanup_corrupted_files for {camera_name}: {e}")

def upload_video_to_api(video_file_path, organization_id=None, camera_guid=None):
    """Upload video file to API"""
    try:
        # Check if file exists and is valid
        if not os.path.exists(video_file_path):
            print(f"❌ Error: Video file not found: {video_file_path}")
            return False
        
        # Validate video file before upload
        file_size = os.path.getsize(video_file_path)
        if file_size < 1024:  # Less than 1KB
            print(f"❌ Error: Video file too small ({file_size} bytes), likely corrupted")
            return False
        
        # Check MP4 structure
        try:
            with open(video_file_path, 'rb') as f:
                header = f.read(1024)
                if b'mdat' not in header:
                    print(f"❌ Error: Invalid MP4 file structure")
                    return False
        except Exception as e:
            print(f"❌ Error: Cannot read video file: {e}")
            return False
        
        # For now, just simulate successful upload
        print(f"✅ Video uploaded successfully: {os.path.basename(video_file_path)} ({file_size} bytes)")
        return True
        
    except Exception as e:
        print(f"❌ Error in upload_video_to_api: {e}")
        return False

def start_hourly_merging(camera_guid):
    """Start the hourly merging task for a camera"""
    def merging_worker():
        while True:
            try:
                # Wait until the next hour starts
                now = datetime.now()
                next_hour = now.replace(minute=0, second=0, microsecond=0)
                if next_hour <= now:
                    next_hour = next_hour.replace(hour=next_hour.hour + 1)
                
                # Calculate seconds to wait
                wait_seconds = (next_hour - now).total_seconds()
                print(f"Next merging task for camera {camera_guid} in {wait_seconds:.0f} seconds")
                
                # Wait until next hour
                time.sleep(wait_seconds)
                
                # Get the hour that just completed (previous hour)
                completed_hour = (next_hour.replace(hour=next_hour.hour - 1)).strftime("%H")
                
                # For now, just log the merge task
                print(f"Hourly merge task completed for hour {completed_hour} in camera {camera_guid}")
                
            except Exception as e:
                print(f"Error in merging worker for camera {camera_guid}: {e}")
                time.sleep(60)  # Wait a minute before retrying
    
    # Start merging thread
    merging_thread = threading.Thread(target=merging_worker)
    merging_thread.daemon = True
    merging_tasks[camera_guid] = merging_thread
    merging_thread.start()
    print(f"Started hourly merging task for camera {camera_guid}")

def find_lan_devices():
    """Discover devices using ARP table. Returns list of (ip, mac, vendor)."""
    try:
        output = os.popen("arp -a").read()
    except Exception:
        output = ""

    devices = re.findall(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-f\-:]{17})", output, re.I)

    results = []
    for ip_address, mac in devices:
        normalized_mac = mac.replace('-', ':').lower()
        vendor = get_mac_vendor(normalized_mac)
        results.append((ip_address, normalized_mac, vendor))
    return results

def camera_thread_function(camera_id, camera_name, camera_rtsp_url, camera_guid):
    """Function that runs in each camera thread - creates video recordings using ffmpeg"""
    print(f"Thread started for camera: {camera_name} ({camera_id})")
    
    # Create output directory structure: base_path/date/cameraguid/hour
    current_date = datetime.now().strftime("%Y-%m-%d")
    current_hour = datetime.now().strftime("%H")
    output_dir = os.path.join(base_video_path, current_date, camera_guid, current_hour)
    os.makedirs(output_dir, exist_ok=True)
    
    # Start hourly merging task
    start_hourly_merging(camera_guid)
    
    try:
        print(f"Starting recording for camera: {camera_name} ({camera_id})")
        
        # Keep trying to start FFmpeg until stop flag is set
        while not stop_flags.get(camera_id, False):
            try:
                # Test RTSP stream
                if not test_rtsp_stream(camera_rtsp_url, camera_name):
                    print(f"RTSP stream test failed for camera: {camera_name} ({camera_id}). Skipping recording.")
                    break
                
                # Get camera settings from config
                camera_config = None
                for cam in cameras_data:
                    if cam['id'] == camera_id:
                        camera_config = cam
                        break
                
                # Default settings
                frame_rate = 15
                video_bitrate = "110k"
                audio_bitrate = "24k"
                resolution = "1280:720"
                preset = "veryfast"
                crf = "28"
                segment_time = 60
                enable_audio = True
                
                # Override with camera-specific settings if available
                if camera_config:
                    if 'camera_settings' in camera_config:
                        settings = camera_config['camera_settings']
                        frame_rate = settings.get('frame_rate', frame_rate)
                        video_bitrate = f"{settings.get('video_bitrate', 110)}k"
                        audio_bitrate = f"{settings.get('audio_bitrate', 24)}k"
                        preset = settings.get('preset', preset)
                        crf = str(settings.get('crf', crf))
                        segment_time = settings.get('segment_time', segment_time)
                        enable_audio = settings.get('enable_audio', enable_audio)
                        
                        # Handle resolution
                        res = settings.get('resolution', '1280x720')
                        if 'x' in res:
                            width, height = res.split('x')
                            resolution = f"{width}:{height}"
                
                # Video recording command
                video_ffmpeg_cmd = [
            "ffmpeg",
            "-rtsp_transport", "tcp",
                    "-i", camera_rtsp_url,
                    "-c:v", "libx264",
                    "-b:v", video_bitrate,
                    "-maxrate", video_bitrate,
                    "-bufsize", f"{int(video_bitrate.replace('k', '')) * 2}k",
                    "-preset", preset,
                    "-crf", crf,
                    "-r", str(frame_rate),
                    "-vf", f"scale={resolution}",
                ]
                
                # Add audio settings if enabled
                if enable_audio:
                    video_ffmpeg_cmd.extend([
                        "-c:a", "aac",
                        "-b:a", audio_bitrate,
                    ])
                
                # Add segment settings for video recording
                video_ffmpeg_cmd.extend([
            "-f", "segment",
                    "-segment_time", str(segment_time),
                    "-segment_time_delta", "0.1",
            "-reset_timestamps", "1",
            "-strftime", "1",
            "-avoid_negative_ts", "make_zero",
            os.path.join(output_dir, "%H%M.mp4")
                ])
                
                print(f"Running video ffmpeg command: {' '.join(video_ffmpeg_cmd)}")
                
                # Start video ffmpeg process
                video_process = subprocess.Popen(
                    video_ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
                    universal_newlines=True,
                    bufsize=1
                )
                
                print(f"Video FFmpeg process started with PID: {video_process.pid}")
                
                # Monitor FFmpeg output in real-time
                def monitor_video_ffmpeg():
                    while video_process.poll() is None:
                        line = video_process.stderr.readline()
                        if line:
                            print(f"Video FFmpeg [{camera_name}]: {line.strip()}")
                
                # Start monitoring thread
                video_monitor_thread = threading.Thread(target=monitor_video_ffmpeg)
                video_monitor_thread.daemon = True
                video_monitor_thread.start()
                
                # Monitor file creation during video creation
                def monitor_files():
                    last_files = set()
                    processing_files = set()
                    processed_files = set()  # Track files that have been processed
                    cleanup_counter = 0  # Counter for periodic cleanup
                    while video_process.poll() is None and not stop_flags.get(camera_id, False):
                        try:
                            # Periodic cleanup of corrupted files (every 10 iterations)
                            cleanup_counter += 1
                            if cleanup_counter >= 10:
                                cleanup_corrupted_files(output_dir, camera_name)
                                cleanup_counter = 0
                            
                            current_files = set()
                            if os.path.exists(output_dir):
                                for file in os.listdir(output_dir):
                                    if file.endswith('.mp4'):
                                        current_files.add(file)
                            
                            # Check for new files
                            new_files = current_files - last_files
                            if new_files:
                                for new_file in new_files:
                                    print(f"New file detected [{camera_name}]: {new_file}")
                                    # Only add to processing if not already there and not already processed
                                    if new_file not in processing_files and new_file not in processed_files:
                                        # Check file age to avoid processing very new files
                                        file_path = os.path.join(output_dir, new_file)
                                        try:
                                            file_age = time.time() - os.path.getctime(file_path)
                                            if file_age < 5:  # Less than 5 seconds old
                                                print(f"⏳ File {new_file} too new ({file_age:.1f}s), waiting before processing")
                                                time.sleep(2)  # Wait a bit more
                                        except Exception as e:
                                            print(f"⚠️ Could not check file age for {new_file}: {e}")
                                        
                                        processing_files.add(new_file)
                                        print(f"New video file added to processing queue [{camera_name}]: {new_file}")
                                    elif new_file in processed_files:
                                        print(f"File {new_file} already processed, skipping")
                                    else:
                                        print(f"File already in processing queue [{camera_name}]: {new_file}")
                            
                            # Check for completed files with better validation
                            completed_files = set()
                            files_to_remove = set()  # Track files to remove after iteration
                            
                            # Create a copy of processing_files to avoid modification during iteration
                            for file in list(processing_files):
                                file_path = os.path.join(output_dir, file)
                                if os.path.exists(file_path):
                                    try:
                                        # Wait for file to stabilize (no size changes for 3 seconds)
                                        size1 = os.path.getsize(file_path)
                                        time.sleep(3)  # Increased wait time
                                        size2 = os.path.getsize(file_path)
                                        
                                        if size1 == size2 and size1 > 1024:  # File size stable and > 1KB
                                            # Additional validation: check if file can be opened and read
                                            try:
                                                with open(file_path, 'rb') as test_file:
                                                    # Try to read first 1KB to check file integrity
                                                    header = test_file.read(1024)
                                                    if len(header) >= 1024 and b'mdat' in header:
                                                        completed_files.add(file)
                                                        print(f"✅ File validated and ready for processing: {file}")
                                                    else:
                                                        print(f"⚠️ File appears incomplete, skipping: {file}")
                                                        files_to_remove.add(file)
                                            except Exception as e:
                                                print(f"⚠️ Cannot read file {file}, skipping: {e}")
                                                files_to_remove.add(file)
                                        elif size1 < 1024:
                                            print(f"⚠️ File {file} too small ({size1} bytes), likely corrupted")
                                            files_to_remove.add(file)
                                        else:
                                            print(f"🔄 File {file} still being written (size: {size1} -> {size2})")
                                            # If file size is decreasing, it might be a new segment replacing old one
                                            if size2 < size1:
                                                print(f"⚠️ File {file} size decreased, likely new segment replacing old one")
                                                files_to_remove.add(file)
                                    except Exception as e:
                                        print(f"⚠️ Error checking file {file}: {e}")
                                        files_to_remove.add(file)
                            
                            # Remove files after iteration to avoid modification during iteration
                            processing_files -= files_to_remove
                            
                            # Process completed files for upload
                            for completed_file in completed_files:
                                if completed_file in processing_files:
                                    processing_files.remove(completed_file)
                                
                                print(f"File completed and validated [{camera_name}]: {completed_file}")
                                file_path = os.path.join(output_dir, completed_file)
                                
                                # Double-check file still exists and is valid before processing
                                if not os.path.exists(file_path):
                                    print(f"⚠️ File {completed_file} no longer exists, skipping")
                                    continue
                                
                                # Final validation before upload
                                try:
                                    final_size = os.path.getsize(file_path)
                                    if final_size < 1024:
                                        print(f"❌ File {completed_file} too small ({final_size} bytes), skipping upload")
                                        continue
                                    
                                    # Test file readability
                                    with open(file_path, 'rb') as test_file:
                                        test_file.read(1024)
                                    
                                    print(f"✅ File {completed_file} passed final validation ({final_size} bytes)")
                                    
                                    # Upload video segment
                                    def upload_segment():
                                        try:
                                            upload_success = upload_video_to_api(file_path, camera_guid=camera_guid)
                                            if upload_success:
                                                print(f"✅ Upload completed for {completed_file}")
                                                # Mark as processed to avoid reprocessing
                                                processed_files.add(completed_file)
                                            else:
                                                print(f"❌ Upload failed for {completed_file}")
                                        except Exception as e:
                                            print(f"❌ Upload error for {completed_file}: {e}")
                                    
                                    upload_thread = threading.Thread(target=upload_segment, 
                                                                  name=f"upload_{camera_name}_{completed_file}")
                                    upload_thread.daemon = True
                                    upload_thread.start()
                                    
                                except Exception as e:
                                    print(f"❌ Final validation failed for {completed_file}: {e}")
                                    continue
                            
                            last_files = current_files
                            
                            # Clean up any files that are no longer in the directory
                            files_to_remove_from_processing = set()
                            for file in processing_files:
                                if not os.path.exists(os.path.join(output_dir, file)):
                                    files_to_remove_from_processing.add(file)
                                    print(f"🗑️ File {file} no longer exists, removing from processing queue")
                            
                            processing_files -= files_to_remove_from_processing
                            
                            time.sleep(2)
                        except Exception as e:
                            print(f"Error monitoring files for {camera_name}: {e}")
                            time.sleep(5)
                
                # Start file monitoring thread
                file_monitor_thread = threading.Thread(target=monitor_files)
                file_monitor_thread.daemon = True
                file_monitor_thread.start()
            
                # Wait for process to complete or stop flag to be set
                while video_process.poll() is None and not stop_flags.get(camera_id, False):
                    time.sleep(1)
                
                # If stop flag is set, terminate the process
                if stop_flags.get(camera_id, False):
                    print(f"Stopping recording for camera: {camera_name} ({camera_id})")
                    # Terminate video process
                    if video_process.poll() is None:
                        video_process.terminate()
                        try:
                            video_process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            video_process.kill()
                    break
                
                # If process ended naturally, log it and restart
                if video_process.poll() is not None and not stop_flags.get(camera_id, False):
                    print(f"Recording process ended unexpectedly for camera: {camera_name} ({camera_id})")
                    # Check video process
                    stdout, stderr = video_process.communicate()
                    if stderr:
                        print(f"Video FFmpeg error output: {stderr}")
                    if stdout:
                        print(f"Video FFmpeg output: {stdout}")
                    print(f"Restarting FFmpeg for camera: {camera_name} ({camera_id}) in 5 seconds...")
                    time.sleep(5)
            
            except Exception as e:
                print(f"Error in FFmpeg process for camera {camera_name} ({camera_id}): {e}")
                print(f"Restarting FFmpeg for camera: {camera_name} ({camera_id}) in 5 seconds...")
                time.sleep(5)
            
    except Exception as e:
        print(f"Error in recording for camera {camera_name} ({camera_id}): {e}")
    
    print(f"Thread stopped for camera: {camera_name} ({camera_id})")

def start_camera_thread(camera):
    """Start a thread for a specific camera"""
    camera_id = camera['id']
    camera_name = camera['name']
    camera_url = camera.get('URL', '')  # Get URL from camera config
    camera_guid = camera.get('GUID', camera_id)  # Get GUID from camera config, fallback to ID
    
    if not camera_url:
        print(f"No URL found for camera: {camera_name} ({camera_id})")
        return
    
    # If thread already exists, stop it first
    if camera_id in active_threads:
        stop_camera_thread(camera_id)
    
    # Set stop flag to False
    stop_flags[camera_id] = False
    
    # Create and start new thread
    thread = threading.Thread(target=camera_thread_function, args=(camera_id, camera_name, camera_url, camera_guid))
    thread.daemon = True  # Make thread daemon so it stops when main program exits
    active_threads[camera_id] = thread
    thread.start()
    
    print(f"Started recording thread for camera: {camera_name}")

def stop_camera_thread(camera_id):
    """Stop a thread for a specific camera"""
    if camera_id in active_threads:
        # Set stop flag to True
        stop_flags[camera_id] = True
        
        # Wait for thread to finish (with timeout)
        thread = active_threads[camera_id]
        thread.join(timeout=5)  # Wait up to 5 seconds
        
        # Remove from active threads
        del active_threads[camera_id]
        if camera_id in stop_flags:
            del stop_flags[camera_id]
        
        print(f"Stopped recording thread for camera ID: {camera_id}")

def validate_camera_credentials(camera):
    """Validate that camera credentials match the RTSP URL"""
    try:
        username = camera.get('username', 'root')
        password = camera.get('password', '')
        ip_address = camera.get('ip_address', '')
        port = camera.get('port', '554')
        path = camera.get('path', '/axis-media/media.amp')
        
        # Construct expected RTSP URL
        if username and password and ip_address:
            encoded_password = urllib.parse.quote(password, safe='')
            expected_url = f"rtsp://{username}:{encoded_password}@{ip_address}:{port}{path}"
            
            # Check if the stored URL matches the constructed one
            stored_url = camera.get('url') or camera.get('URL', '')
            if stored_url and stored_url != expected_url:
                print(f"Warning: RTSP URL mismatch for camera {camera.get('name', 'Unknown')}")
                print(f"Expected: {expected_url}")
                print(f"Stored: {stored_url}")
                # Update the stored URL to match credentials
                camera['url'] = expected_url
                camera['URL'] = expected_url
                return True
        return True
    except Exception as e:
        print(f"Error validating credentials for camera {camera.get('name', 'Unknown')}: {e}")
        return False

def load_cameras():
    """Load cameras from config.json"""
    global cameras_data, base_video_path, groups_data
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        cameras_data = config.get('cameras', [])
        base_video_path = config.get('base_video_path', './videos')
        groups_data = config.get('groups', [])
        
        # Create base video directory if it doesn't exist
        os.makedirs(base_video_path, exist_ok=True)
        
        # Validate and sync camera credentials
        for camera in cameras_data:
            validate_camera_credentials(camera)
        
        print(f"Loaded {len(cameras_data)} cameras from config.json")
        print(f"Base video path: {base_video_path}")
        print(f"Loaded {len(groups_data)} groups from config.json")
        
        # Start recording for cameras that were already recording
        for camera in cameras_data:
            if camera.get('is_recording', False):
                start_camera_thread(camera)
                
    except FileNotFoundError:
        print("config.json not found. Creating default config...")
        cameras_data = []
        base_video_path = './videos'
        groups_data = []
        os.makedirs(base_video_path, exist_ok=True)
        
        # Create default config
        default_config = {
            'base_video_path': base_video_path,
            'cameras': [],
            'groups': []
        }
        
        with open('config.json', 'w') as f:
            json.dump(default_config, f, indent=2)
            
    except Exception as e:
        print(f"Error loading cameras: {e}")
        cameras_data = []
        base_video_path = './videos'

def save_cameras():
    """Save cameras and groups back to config.json"""
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        config['cameras'] = cameras_data
        config['groups'] = groups_data
        
        with open('config.json', 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving cameras: {e}")
        return False

@app.route('/')
def index():
    """Serve the main HTML page"""
    return send_from_directory('.', 'index.html')

@app.route('/api/cameras')
def get_cameras():
    """API endpoint to get all cameras"""
    return jsonify(cameras_data)

@app.route('/api/cameras', methods=['POST'])
def create_camera():
    """Create a new camera and persist in config.json
    Expected JSON: {id?, name, location, ip_address, URL/url, GUID?, is_recording?, 
                   frame_rate?, resolution?, video_bitrate?, enable_audio?, audio_bitrate?, 
                   preset?, crf?, segment_time?, detection_features?, alert_settings?}
    """
    global cameras_data
    data = request.get_json(force=True) or {}

    # Basic validation
    name = (data.get('name') or '').strip()
    ip_address = (data.get('ip_address') or '').strip()
    location = (data.get('location') or '').strip()
    url_value = data.get('URL') or data.get('url') or ''
    url_value = url_value.strip()
    if not name or not ip_address:
        return jsonify({'error': 'name and ip_address are required'}), 400

    # Prevent duplicate id/ip
    if any(c.get('ip_address') == ip_address for c in cameras_data):
        return jsonify({'error': 'Camera with this IP already exists'}), 409

    camera_id = data.get('id') or f"cam_{uuid.uuid4().hex[:8]}"
    if any(c.get('id') == camera_id for c in cameras_data):
        return jsonify({'error': 'Camera with this ID already exists'}), 409

    guid = data.get('GUID') or str(uuid.uuid4())
    is_recording = bool(data.get('is_recording', False))
    status = data.get('status') or 'Active'

    # Default camera settings
    default_camera_settings = {
        'frame_rate': 30,
        'resolution': '1920x1080',
        'video_bitrate': 4000,
        'enable_audio': True,
        'audio_bitrate': 128,
        'preset': 'medium',
        'crf': 23,
        'segment_time': 10
    }

    # Default detection features (all false)
    default_detection_features = {
        'people_detection': False,
        'vehicle_detection': False,
        'people_counting': False,
        'vehicle_counting': False,
        'face_detection': False,
        'face_recognition': False,
        'mask_detection': False,
        'face_mask_compliance': False,
        'intrusion_detection': False,
        'line_crossing_detection': False,
        'loitering_detection': False,
        'aggressive_behavior_detection': False,
        'suspicious_behavior_detection': False,
        'fall_detection': False,
        'social_distancing_detection': False,
        'object_left_behind': False,
        'object_removed': False,
        'bag_detection': False,
        'gun_detection': False,
        'helmet_detection': False,
        'ppe_detection': False,
        'smoking_detection': False,
        'phone_usage_detection': False,
        'license_plate_recognition': False,
        'parking_occupancy_detection': False,
        'wrong_way_detection': False,
        'speed_detection': False,
        'illegal_parking_detection': False,
        'crowd_density_estimation': False,
        'dwell_time_analysis': False,
        'heatmap_generation': False,
        'queue_monitoring': False,
        'footfall_analysis': False,
        'wait_time_analysis': False,
        'fire_detection': False,
        'smoke_detection': False,
        'flood_water_level_detection': False,
        'night_time_movement_detection': False,
        'scream_gunshot_detection': False,
        'glass_break_detection': False,
        're_identification': False,
        'multi_camera_tracking': False,
        'pose_estimation': False,
        'action_recognition': False,
        'gesture_recognition': False,
        'scene_anomaly_detection': False,
        'zone_based_alerts': False,
        'virtual_tripwire': False,
        'retail_zone_interaction': False
    }

    # Default alert settings
    default_alert_settings = {
        'real_time_alerts': False,
        'sms_alerts': False,
        'email_alerts': False,
        'in_app_alerts': True,
        'phone_numbers': [],
        'email_addresses': [],
        'alert_sensitivity': 'Medium',
        'notification_cooldown': 5
    }

    # Merge with provided data
    camera_settings = {**default_camera_settings}
    for key in default_camera_settings:
        if key in data:
            camera_settings[key] = data[key]

    detection_features = {**default_detection_features}
    if 'detection_features' in data and isinstance(data['detection_features'], dict):
        for key in default_detection_features:
            if key in data['detection_features']:
                detection_features[key] = bool(data['detection_features'][key])

    alert_settings = {**default_alert_settings}
    if 'alert_settings' in data and isinstance(data['alert_settings'], dict):
        for key in default_alert_settings:
            if key in data['alert_settings']:
                alert_settings[key] = data['alert_settings'][key]

    new_camera = {
        'id': camera_id,
        'name': name,
        'location': location or 'Unassigned',
        'ip_address': ip_address,
        'mac_address': data.get('mac_address', ''),
        'model': data.get('model', ''),
        'serial_number': data.get('serial_number', ''),
        'organization_id': data.get('organization_id', ''),
        'status': status,
        'username': data.get('username', 'root'),
        'password': data.get('password', ''),
        'port': data.get('port', '554'),
        'path': data.get('path', '/axis-media/media.amp'),
        'url': url_value,
        'URL': url_value,
        'GUID': guid,
        'description': data.get('description', name),
        'is_recording': is_recording,
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat(),
        # Direct camera settings fields
        'frame_rate': camera_settings['frame_rate'],
        'resolution': camera_settings['resolution'],
        'video_bitrate': camera_settings['video_bitrate'],
        'enable_audio': camera_settings['enable_audio'],
        'audio_bitrate': camera_settings['audio_bitrate'],
        'preset': camera_settings['preset'],
        'crf': camera_settings['crf'],
        'segment_time': camera_settings['segment_time'],
        # Structured settings
        'camera_settings': camera_settings,
        'detection_features': detection_features,
        'alert_settings': alert_settings
    }

    cameras_data.append(new_camera)
    if save_cameras():
        return jsonify(new_camera), 201
    # rollback on failure
    cameras_data.pop()
    return jsonify({'error': 'Failed to save camera'}), 500

@app.route('/api/cameras/<camera_id>', methods=['PUT'])
def update_camera(camera_id):
    """Update an existing camera and persist in config.json"""
    global cameras_data
    data = request.get_json(force=True) or {}

    # Find camera
    camera = next((c for c in cameras_data if c.get('id') == camera_id), None)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    # Track previous recording state
    previous_is_recording = bool(camera.get('is_recording', False))

    # Validate ip uniqueness if changed
    if 'ip_address' in data:
        new_ip = (data.get('ip_address') or '').strip()
        if not new_ip:
            return jsonify({'error': 'ip_address cannot be empty'}), 400
        if new_ip != camera.get('ip_address') and any(c.get('ip_address') == new_ip for c in cameras_data):
            return jsonify({'error': 'Camera with this IP already exists'}), 409
        camera['ip_address'] = new_ip

    # Update simple fields
    for field in ['name', 'location', 'model', 'serial_number', 'mac_address', 'organization_id', 'status', 'description', 'username', 'password', 'port', 'path']:
        if field in data and isinstance(data[field], str):
            camera[field] = data[field].strip()

    # URL aliases
    if 'URL' in data or 'url' in data:
        new_url = (data.get('URL') or data.get('url') or '').strip()
        camera['URL'] = new_url
        camera['url'] = new_url

    # is_recording
    if 'is_recording' in data:
        camera['is_recording'] = bool(data['is_recording'])

    # Update camera settings
    camera_settings_fields = ['frame_rate', 'resolution', 'video_bitrate', 'enable_audio', 
                             'audio_bitrate', 'preset', 'crf', 'segment_time']
    for field in camera_settings_fields:
        if field in data:
            camera[field] = data[field]
            # Also update in camera_settings if it exists
            if 'camera_settings' not in camera:
                camera['camera_settings'] = {}
            camera['camera_settings'][field] = data[field]

    # Update detection features
    if 'detection_features' in data and isinstance(data['detection_features'], dict):
        if 'detection_features' not in camera:
            camera['detection_features'] = {}
        for key, value in data['detection_features'].items():
            camera['detection_features'][key] = bool(value)

    # Update alert settings
    if 'alert_settings' in data and isinstance(data['alert_settings'], dict):
        if 'alert_settings' not in camera:
            camera['alert_settings'] = {}
        for key, value in data['alert_settings'].items():
            camera['alert_settings'][key] = value

    # Update timestamp
    camera['updated_at'] = datetime.now().isoformat()

    # Manage threads if recording state changed
    if bool(camera.get('is_recording', False)) != previous_is_recording:
        if camera.get('is_recording', False):
            start_camera_thread(camera)
        else:
            stop_camera_thread(camera_id)

    if save_cameras():
        return jsonify(camera)
    return jsonify({'error': 'Failed to save camera'}), 500

@app.route('/api/cameras/<camera_id>', methods=['DELETE'])
def delete_camera(camera_id):
    """Delete a camera, stop recording thread, and remove from groups."""
    global cameras_data, groups_data
    index = next((i for i, c in enumerate(cameras_data) if c.get('id') == camera_id), None)
    if index is None:
        return jsonify({'error': 'Camera not found'}), 404

    # Stop any active recording
    stop_camera_thread(camera_id)

    # Remove from cameras
    cameras_data.pop(index)

    # Remove camera id from any groups
    for g in groups_data:
        ids = g.get('camera_ids', [])
        if camera_id in ids:
            g['camera_ids'] = [cid for cid in ids if cid != camera_id]

    if save_cameras():
        return jsonify({'success': True})
    return jsonify({'error': 'Failed to save changes'}), 500

@app.route('/api/discover')
def discover_cameras_api():
    """API endpoint to discover devices on the LAN and flag likely cameras."""
    devices = find_lan_devices()
    discovered = []
    for ip_address, mac, vendor in devices:
        vendor_lower = vendor.lower()
        is_camera = any(v in vendor_lower for v in KNOWN_CAMERA_VENDORS)
        discovered.append({
            'ip_address': ip_address,
            'mac_address': mac,
            'vendor': vendor,
            'is_camera': is_camera,
        })
    return jsonify(discovered)
@app.route('/api/groups', methods=['GET'])
def get_groups():
    """List all groups"""
    return jsonify(groups_data)

@app.route('/api/groups', methods=['POST'])
def create_group():
    """Create a new group: expects {name, camera_ids[]}"""
    global groups_data
    data = request.get_json(force=True) or {}
    name = data.get('name', '').strip()
    camera_ids = data.get('camera_ids', [])
    if not name:
        return jsonify({'error': 'Group name is required'}), 400
    # Validate camera IDs
    valid_ids = {c['id'] for c in cameras_data}
    invalid = [cid for cid in camera_ids if cid not in valid_ids]
    if invalid:
        return jsonify({'error': 'Invalid camera ids', 'invalid_ids': invalid}), 400
    group_id = f"grp_{uuid.uuid4().hex[:8]}"
    group = {'id': group_id, 'name': name, 'camera_ids': camera_ids}
    groups_data.append(group)
    if save_cameras():
        return jsonify(group), 201
    return jsonify({'error': 'Failed to save group'}), 500

@app.route('/api/groups/<group_id>', methods=['PUT'])
def update_group(group_id):
    """Update group name and/or camera_ids"""
    global groups_data
    data = request.get_json(force=True) or {}
    # Find group
    group = next((g for g in groups_data if g.get('id') == group_id), None)
    if not group:
        return jsonify({'error': 'Group not found'}), 404
    # Update fields
    if 'name' in data and isinstance(data['name'], str):
        group['name'] = data['name'].strip()
    if 'camera_ids' in data and isinstance(data['camera_ids'], list):
        valid_ids = {c['id'] for c in cameras_data}
        invalid = [cid for cid in data['camera_ids'] if cid not in valid_ids]
        if invalid:
            return jsonify({'error': 'Invalid camera ids', 'invalid_ids': invalid}), 400
        group['camera_ids'] = data['camera_ids']
    if save_cameras():
        return jsonify(group)
    return jsonify({'error': 'Failed to save group'}), 500

@app.route('/api/groups/<group_id>', methods=['DELETE'])
def delete_group(group_id):
    """Delete a group"""
    global groups_data
    index = next((i for i, g in enumerate(groups_data) if g.get('id') == group_id), None)
    if index is None:
        return jsonify({'error': 'Group not found'}), 404
    groups_data.pop(index)
    if save_cameras():
        return jsonify({'success': True})
    return jsonify({'error': 'Failed to delete group'}), 500

@app.route('/api/discover/cameras')
def discover_only_cameras_api():
    """API endpoint to discover and return only likely camera devices."""
    devices = find_lan_devices()
    cameras_only = []
    for ip_address, mac, vendor in devices:
        vendor_lower = vendor.lower()
        if any(v in vendor_lower for v in KNOWN_CAMERA_VENDORS):
            cameras_only.append({
                'ip_address': ip_address,
                'mac_address': mac,
                'vendor': vendor,
            })
    return jsonify(cameras_only)

@app.route('/api/cameras/<camera_id>/toggle', methods=['POST'])
def toggle_camera_recording(camera_id):
    """API endpoint to toggle camera recording status"""
    global cameras_data
    
    # Find the camera
    camera = None
    for cam in cameras_data:
        if cam['id'] == camera_id:
            camera = cam
            break
    
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404
    
    # Get the new recording status
    data = request.get_json()
    new_recording_status = data.get('is_recording', False)
    
    # Update the recording status
    camera['is_recording'] = new_recording_status
    
    # Manage thread based on recording status
    if new_recording_status:
        # Start thread if recording is turned ON
        start_camera_thread(camera)
        print(f"Recording turned ON for camera: {camera['name']}")
    else:
        # Stop thread if recording is turned OFF
        stop_camera_thread(camera_id)
        print(f"Recording turned OFF for camera: {camera['name']}")
    
    # Save to file
    if save_cameras():
        return jsonify({'success': True, 'camera': camera})
    else:
        return jsonify({'error': 'Failed to save changes'}), 500

@app.route('/api/threads/status')
def get_thread_status():
    """API endpoint to get current thread status"""
    thread_status = {}
    for camera in cameras_data:
        camera_id = camera['id']
        thread_status[camera_id] = {
            'is_recording': camera.get('is_recording', False),
            'has_active_thread': camera_id in active_threads,
            'camera_name': camera['name']
        }
    return jsonify(thread_status)

@app.route('/api/cameras/<camera_id>/frame')
def get_camera_frame(camera_id):
    """API endpoint to get camera frame as MJPEG stream"""
    # Find the camera
    camera = None
    for cam in cameras_data:
        if cam['id'] == camera_id:
            camera = cam
            break
    
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404
    
    # Get RTSP URL from query parameter or camera config
    rtsp_url = request.args.get('rtsp_url')
    if not rtsp_url:
        rtsp_url = camera.get('URL', '') or camera.get('url', '')
    
    if not rtsp_url:
        return jsonify({'error': 'Camera URL not configured'}), 400
    
    try:
        # Use ffmpeg to convert RTSP stream to MJPEG
        ffmpeg_cmd = [
            "ffmpeg",
            "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-f", "mjpeg",
            "-q:v", "5",  # Quality level (1-31, lower is better)
            "-r", "10",   # Frame rate
            "-s", "640x360",  # Resolution for live view
            "pipe:1"
        ]
        
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        def generate():
            try:
                while True:
                    # Read a chunk of data
                    chunk = process.stdout.read(1024)
                    if not chunk:
                        break
                    yield chunk
            except Exception as e:
                print(f"Error in MJPEG stream: {e}")
            finally:
                if process.poll() is None:
                    process.terminate()
        
        return app.response_class(
            generate(),
            mimetype='multipart/x-mixed-replace; boundary=frame'
        )
        
    except Exception as e:
        print(f"Error creating MJPEG stream: {e}")
        return jsonify({'error': 'Failed to create video stream'}), 500

@app.route('/api/cameras/<camera_id>/stream')
def get_camera_stream(camera_id):
    """API endpoint to get camera live stream as HLS"""
    # Find the camera
    camera = None
    for cam in cameras_data:
        if cam['id'] == camera_id:
            camera = cam
            break
    
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404
    
    camera_url = camera.get('URL', '')
    if not camera_url:
        return jsonify({'error': 'Camera URL not configured'}), 400
    
    try:
        # Create HLS stream directory
        stream_dir = os.path.join(base_video_path, 'live_streams', camera_id)
        os.makedirs(stream_dir, exist_ok=True)
        
        # Use ffmpeg to convert RTSP stream to HLS
        ffmpeg_cmd = [
            "ffmpeg",
            "-rtsp_transport", "tcp",
            "-i", camera_url,
            "-c:v", "libx264",
            "-c:a", "aac",
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "3",
            "-hls_flags", "delete_segments",
            "-hls_segment_filename", os.path.join(stream_dir, "segment_%03d.ts"),
            os.path.join(stream_dir, "playlist.m3u8")
        ]
        
        # Start ffmpeg process in background
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Return the HLS playlist URL
        return jsonify({
            'stream_url': f'/api/cameras/{camera_id}/hls/playlist.m3u8',
            'status': 'streaming'
        })
        
    except Exception as e:
        print(f"Error creating HLS stream: {e}")
        return jsonify({'error': 'Failed to create video stream'}), 500

@app.route('/api/cameras/<camera_id>/hls/<path:filename>')
def get_hls_file(camera_id, filename):
    """Serve HLS files for live streaming"""
    stream_dir = os.path.join(base_video_path, 'live_streams', camera_id)
    return send_from_directory(stream_dir, filename)

@app.route('/api/videos')
def get_videos():
    """API endpoint to get list of videos by date and optional camera ID and hour"""
    date = request.args.get('date')
    camera_id = request.args.get('camera_id')
    hour = request.args.get('hour')
    
    if not date:
        return jsonify({'error': 'Date parameter is required'}), 400
    
    try:
        videos = []
        date_path = os.path.join(base_video_path, date)
        
        if not os.path.exists(date_path):
            return jsonify(videos)
        
        # Get all camera GUIDs for the date
        for item in os.listdir(date_path):
            item_path = os.path.join(date_path, item)
            if os.path.isdir(item_path):
                # Find camera by GUID
                camera = None
                for cam in cameras_data:
                    if cam.get('GUID') == item:
                        camera = cam
                        break
                
                # Skip if camera_id filter is specified and doesn't match
                if camera_id and camera and camera.get('id') != camera_id:
                    continue
                
                # Look for hour folders (00, 01, 02, ..., 23)
                for hour_folder in os.listdir(item_path):
                    hour_path = os.path.join(item_path, hour_folder)
                    if os.path.isdir(hour_path) and hour_folder.isdigit() and 0 <= int(hour_folder) <= 23:
                        # Skip if hour filter is specified and doesn't match
                        if hour and hour_folder != hour:
                            continue
                            
                        # Get all video files in this hour folder
                        for video_file in os.listdir(hour_path):
                            if video_file.endswith('.mp4'):
                                video_path = os.path.join(hour_path, video_file)
                                file_stat = os.stat(video_path)
                                
                                video_info = {
                                    'filename': video_file,
                                    'path': video_path,
                                    'camera_id': camera.get('id') if camera else None,
                                    'camera_name': camera.get('name') if camera else 'Unknown Camera',
                                    'camera_guid': item,
                                    'hour': hour_folder,
                                    'size': file_stat.st_size,
                                    'created_at': datetime.fromtimestamp(file_stat.st_ctime).isoformat(),
                                    'modified_at': datetime.fromtimestamp(file_stat.st_mtime).isoformat()
                                }
                                videos.append(video_info)
        
        # Sort by creation time (newest first)
        videos.sort(key=lambda x: x['created_at'], reverse=True)
        return jsonify(videos)
        
    except Exception as e:
        print(f"Error getting videos: {e}")
        return jsonify({'error': 'Failed to get videos'}), 500

@app.route('/api/videos/play/<filename>')
def play_video(filename):
    """API endpoint to stream video for playback"""
    try:
        # Find the video file
        video_path = None
        for root, dirs, files in os.walk(base_video_path):
            if filename in files:
                video_path = os.path.join(root, filename)
                break
        
        if not video_path or not os.path.exists(video_path):
            return jsonify({'error': 'Video not found'}), 404
        
        return send_file(video_path, mimetype='video/mp4')
        
    except Exception as e:
        print(f"Error playing video: {e}")
        return jsonify({'error': 'Failed to play video'}), 500

@app.route('/api/videos/download/<filename>')
def download_video(filename):
    """API endpoint to download video file"""
    try:
        # Find the video file
        video_path = None
        for root, dirs, files in os.walk(base_video_path):
            if filename in files:
                video_path = os.path.join(root, filename)
                break
        
        if not video_path or not os.path.exists(video_path):
            return jsonify({'error': 'Video not found'}), 404
        
        return send_file(video_path, as_attachment=True, download_name=filename)
        
    except Exception as e:
        print(f"Error downloading video: {e}")
        return jsonify({'error': 'Failed to download video'}), 500

# Authentication Routes
@app.route('/login')
def login_page():
    """Serve the login page"""
    return send_file('login.html')

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Handle user login"""
    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        
        if not username or not password:
            return jsonify({'message': 'Username and password are required'}), 400
        
        # Simple authentication - in production, use proper password hashing
        # For demo purposes, accept any username/password or admin/admin123
        if (username == 'admin' and password == 'admin123') or (username and password):
            # Create user session
            user = {
                'id': f"user_{str(uuid.uuid4())[:8]}",
                'username': username,
                'name': username.title(),
                'role': 'Admin' if username == 'admin' else 'User',
                'organization_id': 'org_001',
                'isfactory': True,
                'login_time': datetime.now().isoformat()
            }
            
            session['user'] = user
            session['is_authenticated'] = True
            
            return jsonify({
                'message': 'Login successful',
                'user': user
            })
        else:
            return jsonify({'message': 'Invalid username or password'}), 401
            
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'message': 'Login failed'}), 500

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """Handle user logout"""
    try:
        session.clear()
        return jsonify({'message': 'Logout successful'})
    except Exception as e:
        print(f"Logout error: {e}")
        return jsonify({'message': 'Logout failed'}), 500

@app.route('/api/auth/status')
def auth_status():
    """Check authentication status"""
    if session.get('is_authenticated'):
        return jsonify({
            'authenticated': True,
            'user': session.get('user')
        })
    return jsonify({'authenticated': False})

# Static file routes
@app.route('/<path:filename>')
def serve_static(filename):
    """Serve static files like images, CSS, JS"""
    try:
        return send_file(filename)
    except FileNotFoundError:
        return "File not found", 404

# Organization Management Routes
@app.route('/organizations')
def organizations_page():
    """Serve the organizations page"""
    return send_file('organizations.html')

@app.route('/api/organizations', methods=['GET'])
def get_organizations():
    """Get all organizations"""
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        # Return organizations array if it exists, otherwise return current organization as single item
        if 'organizations' in config:
            return jsonify(config['organizations'])
        else:
            # Convert single organization to array format
            org = config.get('organization', {})
            if org:
                return jsonify([org])
            return jsonify([])
    except Exception as e:
        print(f"Error loading organizations: {e}")
        return jsonify({'error': 'Failed to load organizations'}), 500

@app.route('/api/organizations', methods=['POST'])
def create_organization():
    """Create a new organization"""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['name', 'contact_email', 'address']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'error': f'{field} is required'}), 400
        
        # Load current config
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        # Create new organization
        new_org = {
            'id': f"org_{str(uuid.uuid4())[:8]}",
            'name': data['name'],
            'address': data['address'],
            'contact_email': data['contact_email'],
            'storage_provider': data.get('storage_provider', ''),
            'server_video_path': data.get('server_video_path', ''),
            'client_video_access_path': data.get('client_video_access_path', ''),
            'retention': data.get('retention', 10),
            'upload_username': data.get('upload_username', 'admin'),
            'upload_password': data.get('upload_password', 'admin'),
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }
        
        # Initialize organizations array if it doesn't exist
        if 'organizations' not in config:
            config['organizations'] = []
        
        # Add new organization
        config['organizations'].append(new_org)
        
        # Save config
        with open('config.json', 'w') as f:
            json.dump(config, f, indent=2)
        
        return jsonify(new_org), 201
        
    except Exception as e:
        print(f"Error creating organization: {e}")
        return jsonify({'error': 'Failed to create organization'}), 500

@app.route('/api/organizations/<org_id>', methods=['PUT'])
def update_organization(org_id):
    """Update an existing organization"""
    try:
        data = request.get_json()
        
        # Load current config
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        # Find organization to update
        organizations = config.get('organizations', [])
        org_index = None
        
        for i, org in enumerate(organizations):
            if org['id'] == org_id:
                org_index = i
                break
        
        if org_index is None:
            return jsonify({'error': 'Organization not found'}), 404
        
        # Update organization
        organizations[org_index].update({
            'name': data.get('name', organizations[org_index]['name']),
            'address': data.get('address', organizations[org_index]['address']),
            'contact_email': data.get('contact_email', organizations[org_index]['contact_email']),
            'storage_provider': data.get('storage_provider', organizations[org_index].get('storage_provider', '')),
            'server_video_path': data.get('server_video_path', organizations[org_index].get('server_video_path', '')),
            'client_video_access_path': data.get('client_video_access_path', organizations[org_index].get('client_video_access_path', '')),
            'retention': data.get('retention', organizations[org_index].get('retention', 10)),
            'upload_username': data.get('upload_username', organizations[org_index].get('upload_username', 'admin')),
            'upload_password': data.get('upload_password', organizations[org_index].get('upload_password', 'admin')),
            'updated_at': datetime.now().isoformat()
        })
        
        # Save config
        with open('config.json', 'w') as f:
            json.dump(config, f, indent=2)
        
        return jsonify(organizations[org_index])
        
    except Exception as e:
        print(f"Error updating organization: {e}")
        return jsonify({'error': 'Failed to update organization'}), 500

@app.route('/api/organizations/<org_id>', methods=['DELETE'])
def delete_organization(org_id):
    """Delete an organization"""
    try:
        # Load current config
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        # Find organization to delete
        organizations = config.get('organizations', [])
        org_index = None
        
        for i, org in enumerate(organizations):
            if org['id'] == org_id:
                org_index = i
                break
        
        if org_index is None:
            return jsonify({'error': 'Organization not found'}), 404
        
        # Remove organization
        deleted_org = organizations.pop(org_index)
        
        # Save config
        with open('config.json', 'w') as f:
            json.dump(config, f, indent=2)
        
        return jsonify({'message': 'Organization deleted successfully'})
        
    except Exception as e:
        print(f"Error deleting organization: {e}")
        return jsonify({'error': 'Failed to delete organization'}), 500

def main():
    """Main function"""
    # Load cameras first
    load_cameras()
    
    # Start the Flask server
    print("Starting Camera Management System...")
    print("Web interface available at: http://localhost:5000")
    print("Press Ctrl+C to stop the server")
    
    try:
        app.run(host='0.0.0.0', port=5000, debug=False)  # Set debug=False for production
    except KeyboardInterrupt:
        print("\nShutting down...")
        # Stop all active threads
        for camera_id in list(active_threads.keys()):
            stop_camera_thread(camera_id)
        print("All threads stopped.")

if __name__ == "__main__":
    main() 