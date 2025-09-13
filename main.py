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
import queue
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_from_directory, send_file

app = Flask(__name__)

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
# Dictionary to store merging tasks for each camera
merging_tasks = {}
# Global merge queue and worker thread
merge_queue = queue.Queue()
merge_worker_thread = None
merge_worker_stop_flag = False
# Global variable for API configuration
api_organization_id = 14  # Default organization ID
# Configuration for video storage and upload
video_config = {
    'store_locally': True,  # Store videos locally
    'upload_to_api': True,  # Upload videos to FastAPI server
    'api_url': 'http://127.0.0.1:8000/api/video/upload',
    'local_storage_path': './local_videos',  # Local storage path
    'keep_local_copies': True,  # Keep local copies after API upload
    'cleanup_after_upload': False  # Delete local files after successful upload
}

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

def generate_thumbnail(mp4_path, thumbnail_path=None, quality=2):
    """Generate thumbnail from MP4 file with better error handling for corrupted files"""
    try:
        # Validate input path
        if not os.path.exists(mp4_path):
            print(f"Error: MP4 file not found at {mp4_path}")
            return None
        
        # Check file size - reject files that are too small
        file_size = os.path.getsize(mp4_path)
        if file_size < 1024:
            print(f"Error: MP4 file too small ({file_size} bytes), likely corrupted")
            return None
        
        print(f"Generating thumbnail for {mp4_path} (size: {file_size} bytes)")
        
        # Set default thumbnail path if not provided
        if thumbnail_path is None:
            video_dir = os.path.dirname(mp4_path)
            video_name = os.path.splitext(os.path.basename(mp4_path))[0]
            thumbnail_path = os.path.join(video_dir, f"{video_name}.jpg")
        
        # FFmpeg command to extract thumbnail at 1 second mark
        ffmpeg_cmd = [
            "ffmpeg",
            "-i", mp4_path,
            "-ss", "00:00:01",  # Seek to 1 second
            "-vframes", "1",     # Capture 1 frame
            "-q:v", str(quality), # Quality (2 is good)
            "-y",                # Overwrite if exists
            thumbnail_path
        ]
        
        print(f"Creating thumbnail for {mp4_path}")
        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=30)
        
        # Verify thumbnail was created
        if os.path.exists(thumbnail_path) and os.path.getsize(thumbnail_path) > 0:
            thumbnail_size = os.path.getsize(thumbnail_path)
            print(f"Successfully created thumbnail at {thumbnail_path} (size: {thumbnail_size} bytes)")
            return thumbnail_path
        
        return None
        
    except subprocess.TimeoutExpired:
        print(f"Error: FFmpeg thumbnail generation timed out")
        return None
    except Exception as e:
        print(f"Error creating thumbnail: {e}")
        return None

def upload_video_to_api(video_file_path, organization_id=None, camera_guid=None):
    """Upload video to API and optionally store locally with better error handling and validation"""
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
        
        # Step 1: Store locally if configured
        local_copy_path = None
        if video_config['store_locally']:
            try:
                # Create local storage directory structure
                local_dir = os.path.join(
                    video_config['local_storage_path'],
                    str(organization_id or api_organization_id),
                    camera_guid or 'unknown',
                    datetime.now().strftime("%Y-%m-%d"),
                    datetime.now().strftime("%H")
                )
                os.makedirs(local_dir, exist_ok=True)
                
                # Copy video file to local storage
                local_filename = os.path.basename(video_file_path)
                local_copy_path = os.path.join(local_dir, local_filename)
                
                import shutil
                shutil.copy2(video_file_path, local_copy_path)
                print(f"✅ Video stored locally: {local_copy_path}")
                
            except Exception as e:
                print(f"⚠️ Warning: Failed to store video locally: {e}")
                local_copy_path = None
        
        # Step 2: Generate thumbnail if configured
        thumbnail_path = None
        if video_config['upload_to_api']:
            try:
                # Always attempt thumbnail generation for API upload
                video_dir = os.path.dirname(video_file_path)
                video_name = os.path.splitext(os.path.basename(video_file_path))[0]
                thumbnail_path = os.path.join(video_dir, f"{video_name}.jpg")
                
                print(f"🔄 Generating thumbnail for: {os.path.basename(video_file_path)}")
                generated_thumbnail_path = generate_thumbnail(video_file_path, thumbnail_path)
                    
            except Exception as e:
                print(f"⚠️ Warning: Failed to generate thumbnail: {e}")
                thumbnail_path = None
        
        # Step 3: Upload to API with better error handling
        api_upload_success = False
        if video_config['upload_to_api']:
            print("🚀 Proceeding with video upload to API...")
            try:
                # Use context managers to ensure files are properly closed
                with open(video_file_path, 'rb') as video_file:
                    files = {
                        'video_file': (os.path.basename(video_file_path), video_file, 'video/mp4')
                    }
                    
                    # Add thumbnail if available
                    if thumbnail_path and os.path.exists(thumbnail_path):
                        with open(thumbnail_path, 'rb') as thumb_file:
                            files['thumbnail_file'] = (os.path.basename(thumbnail_path), thumb_file, 'image/jpeg')
                    
                    # Prepare form data
                    data = {
                        'organization_id': str(organization_id or api_organization_id),
                    }
                    if camera_guid:
                        data['guid'] = camera_guid
                    
                    print(f"📤 Uploading video: {os.path.basename(video_file_path)} ({file_size} bytes)")
                    
                    # Upload with retry logic
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            response = requests.post(
                                video_config['api_url'], 
                                files=files, 
                                data=data, 
                                timeout=300
                            )
                            
                            if response.status_code == 200:
                                print(f"✅ API upload successful on attempt {attempt + 1}")
                                api_upload_success = True
                                break
                            else:
                                print(f"⚠️ Upload attempt {attempt + 1} failed: HTTP {response.status_code}")
                                if attempt < max_retries - 1:
                                    print(f"🔄 Retrying in 5 seconds...")
                                    time.sleep(5)
                                    
                        except requests.exceptions.Timeout:
                            print(f"⚠️ Upload attempt {attempt + 1} timed out")
                            if attempt < max_retries - 1:
                                print(f"🔄 Retrying in 5 seconds...")
                                time.sleep(5)
                        except Exception as e:
                            print(f"❌ Upload attempt {attempt + 1} failed: {e}")
                            if attempt < max_retries - 1:
                                print(f"🔄 Retrying in 5 seconds...")
                                time.sleep(5)
                    
                    if not api_upload_success:
                        print(f"❌ All upload attempts failed")
                        
            except Exception as e:
                print(f"❌ Error uploading to API: {e}")
                api_upload_success = False
        
        # Step 4: Summary and return
        if video_config['store_locally'] and video_config['upload_to_api']:
            if api_upload_success:
                print(f"✅ Video processed successfully: Local storage + API upload")
                return True
            else:
                print(f"⚠️ Video partially processed: Local storage only (API upload failed)")
                return True  # Still consider it successful since we have a local copy
        elif video_config['store_locally']:
            print(f"✅ Video stored locally only")
            return True
        elif video_config['upload_to_api']:
            if api_upload_success:
                print(f"✅ Video uploaded to API successfully")
                return True
            else:
                print(f"❌ Video upload to API failed")
                return False
        else:
            print(f"⚠️ Warning: No storage method configured")
            return False
            
    except Exception as e:
        print(f"❌ Error in upload_video_to_api: {e}")
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
                os.remove(file_path)
                print(f"🗑️ Cleaned up corrupted file [{camera_name}]: {os.path.basename(file_path)} - {reason}")
            except Exception as e:
                print(f"⚠️ Failed to remove corrupted file {file_path}: {e}")
        
        if corrupted_files:
            print(f"🧹 Cleaned up {len(corrupted_files)} corrupted files for camera {camera_name}")
        
    except Exception as e:
        print(f"Error in cleanup_corrupted_files for {camera_name}: {e}")

def camera_thread_function(camera_id, camera_name, camera_rtsp_url, camera_guid):
    """Function that runs in each camera thread - creates video recordings using ffmpeg"""
    print(f"Thread started for camera: {camera_name} ({camera_id})")
    
    # Create output directory structure: base_path/date/cameraguid/hh/mm.mp4
    current_date = datetime.now().strftime("%Y-%m-%d")
    current_hour = datetime.now().strftime("%H")
    output_dir = os.path.join(base_video_path, current_date, camera_guid, current_hour)
    os.makedirs(output_dir, exist_ok=True)
    
    try:
        print(f"Starting recording for camera: {camera_name} ({camera_id})")
        
        # Build ffmpeg command - this will run continuously and create segments every 60 seconds
        ffmpeg_cmd = [
            "ffmpeg",
            "-rtsp_transport", "tcp",
            "-i", camera_url,
            "-c", "copy",
            "-f", "segment",
            "-segment_time", "60",
            "-reset_timestamps", "1",
            "-strftime", "1",
            os.path.join(output_dir, "segment_%Y%m%d_%H%M%S.mp4")
        ]
        
        print(f"Running ffmpeg command: {' '.join(ffmpeg_cmd)}")
        
        # Start ffmpeg process - this will run continuously and create new files every minute
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        
        # Monitor for hour changes and restart ffmpeg with new folder
        last_hour = current_hour
        
        while process.poll() is None and not stop_flags.get(camera_id, False):
            time.sleep(30)  # Check every 30 seconds
            
            # Check if hour has changed
            current_hour = datetime.now().strftime("%H")
            if current_hour != last_hour:
                print(f"Hour changed from {last_hour} to {current_hour} for camera: {camera_name}")
                
                # Stop current ffmpeg process
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                
                # Create new hour folder
                new_output_dir = os.path.join(base_video_path, current_date, camera_guid, current_hour)
                os.makedirs(new_output_dir, exist_ok=True)
                
                # Build new ffmpeg command with new folder
                ffmpeg_cmd = [
                    "ffmpeg",
                    "-rtsp_transport", "tcp",
                    "-i", camera_url,
                    "-c", "copy",
                    "-f", "segment",
                    "-segment_time", "60",
                    "-reset_timestamps", "1",
                    "-strftime", "1",
                    os.path.join(new_output_dir, "%H%M.mp4")
                ]
                
                print(f"Restarting ffmpeg with new hour folder: {new_output_dir}")
                
                # Start new ffmpeg process
                process = subprocess.Popen(
                    ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True
                )
                
                last_hour = current_hour
        
        # If stop flag is set, terminate the process
        if stop_flags.get(camera_id, False):
            print(f"Stopping recording for camera: {camera_name} ({camera_id})")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        
        # If process ended naturally (not due to stop flag), log it
        if process.poll() is not None and not stop_flags.get(camera_id, False):
            print(f"Recording process ended unexpectedly for camera: {camera_name} ({camera_id})")
            
    except Exception as e:
        print(f"Error in recording for camera {camera_name} ({camera_id}): {e}")
    
    print(f"Thread stopped for camera: {camera_name} ({camera_id})")

def start_camera_thread(camera):
    """Start a thread for a specific camera"""
    camera_id = camera['id']
    camera_name = camera['name']
    camera_rtsp_url = camera.get('URL', '')  # Get URL from camera config
    camera_guid = camera.get('GUID', camera_id)  # Get GUID from camera config, fallback to ID
    
    if not camera_rtsp_url:
        print(f"No RTSP URL found for camera: {camera_name} ({camera_id})")
        return
    
    # If thread already exists, stop it first
    if camera_id in active_threads:
        stop_camera_thread(camera_id)
    
    # Set stop flag to False
    stop_flags[camera_id] = False
    
    # Create and start new thread
    thread = threading.Thread(target=camera_thread_function, args=(camera_id, camera_name, camera_rtsp_url, camera_guid))
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

def load_cameras():
    """Load cameras from config.json"""
    global cameras_data, base_video_path, groups_data, api_organization_id, video_config
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        cameras_data = config.get('cameras', [])
        base_video_path = config.get('base_video_path', './videos')
        groups_data = config.get('groups', [])
        
        # Get organization ID from config
        org = config.get('organization', {})
        if org and 'id' in org:
            api_organization_id = org['id']
            print(f"Organization ID loaded from config: {api_organization_id}")
        
        # Load video configuration from config
        if 'video_config' in config:
            video_config.update(config['video_config'])
            print("Video configuration loaded from config.json")
        
        # Create base video directory if it doesn't exist
        os.makedirs(base_video_path, exist_ok=True)
        
        # Create local storage directory if enabled
        if video_config['store_locally']:
            os.makedirs(video_config['local_storage_path'], exist_ok=True)
            print(f"Local storage directory created: {video_config['local_storage_path']}")
        
        print(f"Loaded {len(cameras_data)} cameras from config.json")
        print(f"Base video path: {base_video_path}")
        print(f"Organization ID: {api_organization_id}")
        print(f"Local storage: {'Enabled' if video_config['store_locally'] else 'Disabled'}")
        print(f"API upload: {'Enabled' if video_config['upload_to_api'] else 'Disabled'}")
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
        
        # Create local storage directory if enabled
        if video_config['store_locally']:
            os.makedirs(video_config['local_storage_path'], exist_ok=True)
        
        # Create default config
        default_config = {
            'base_video_path': base_video_path,
            'cameras': [],
            'groups': [],
            'video_config': video_config
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
        config['video_config'] = video_config
        
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
        'model': data.get('model', ''),
        'organization_id': data.get('organization_id', ''),
        'status': status,
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
    for field in ['name', 'location', 'model', 'organization_id', 'status', 'description']:
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
    
    camera_url = camera.get('URL', '')
    if not camera_url:
        return jsonify({'error': 'Camera URL not configured'}), 400
    
    try:
        # Use ffmpeg to convert RTSP stream to MJPEG
        ffmpeg_cmd = [
            "ffmpeg",
            "-rtsp_transport", "tcp",
            "-i", camera_url,
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

@app.route('/api/video/upload/status')
def get_upload_status():
    """API endpoint to get video upload configuration and status"""
    try:
        return jsonify({
            'api_url': video_config['api_url'],
            'organization_id': api_organization_id,
            'upload_enabled': video_config['upload_to_api'],
            'local_storage_enabled': video_config['store_locally'],
            'local_storage_path': video_config['local_storage_path'],
            'keep_local_copies': video_config['keep_local_copies'],
            'cleanup_after_upload': video_config['cleanup_after_upload'],
            'timeout_seconds': 300,
            'upload_types': {
                '1_minute_segments': 'Automatically uploaded when created',
                'hourly_videos': 'Automatically uploaded after merging'
            },
            'message': f"Videos are {'stored locally and ' if video_config['store_locally'] else ''}{'uploaded to API' if video_config['upload_to_api'] else 'not uploaded'}"
        })
    except Exception as e:
        return jsonify({'error': f'Error getting upload status: {str(e)}'}), 500

@app.route('/api/video/config', methods=['GET', 'POST'])
def manage_video_config():
    """API endpoint to get and update video configuration"""
    global video_config
    
    if request.method == 'GET':
        return jsonify({
            'video_config': video_config,
            'message': 'Current video storage and upload configuration'
        })
    
    elif request.method == 'POST':
        try:
            data = request.get_json()
            
            # Update configuration fields
            if 'store_locally' in data:
                video_config['store_locally'] = bool(data['store_locally'])
            if 'upload_to_api' in data:
                video_config['upload_to_api'] = bool(data['upload_to_api'])
            if 'api_url' in data:
                video_config['api_url'] = str(data['api_url'])
            if 'local_storage_path' in data:
                video_config['local_storage_path'] = str(data['local_storage_path'])
            if 'keep_local_copies' in data:
                video_config['keep_local_copies'] = bool(data['keep_local_copies'])
            if 'cleanup_after_upload' in data:
                video_config['cleanup_after_upload'] = bool(data['cleanup_after_upload'])

            # Create local storage directory if enabled
            if video_config['store_locally']:
                os.makedirs(video_config['local_storage_path'], exist_ok=True)
            
            return jsonify({
                'success': True,
                'video_config': video_config,
                'message': 'Video configuration updated successfully'
            })
            
        except Exception as e:
            return jsonify({'error': f'Error updating video configuration: {str(e)}'}), 500

@app.route('/api/cameras/<camera_id>/files')
def get_camera_files(camera_id):
    """API endpoint to get current video files for a camera"""
    # Find the camera
    camera = None
    for cam in cameras_data:
        if cam['id'] == camera_id:
            camera = cam
            break
    
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404
    
    try:
        camera_guid = camera.get('GUID', camera_id)
        current_date = datetime.now().strftime("%Y-%m-%d")
        segment_dir = os.path.join(base_video_path, current_date, camera_guid, "mp4")
        
        if not os.path.exists(segment_dir):
            return jsonify({
                'camera_id': camera_id,
                'camera_name': camera['name'],
                'files': [],
                'directory': segment_dir,
                'exists': False
            })
        
        # Get all mp4 files in the directory
        files = []
        for file in os.listdir(segment_dir):
            if file.endswith('.mp4'):
                file_path = os.path.join(segment_dir, file)
                file_stat = os.stat(file_path)
                files.append({
                    'name': file,
                    'size': file_stat.st_size,
                    'modified': datetime.fromtimestamp(file_stat.st_mtime).isoformat(),
                    'path': file_path
                })
        
        # Sort files by name
        files.sort(key=lambda x: x['name'])
        
        return jsonify({
            'camera_id': camera_id,
            'camera_name': camera['name'],
            'files': files,
            'directory': segment_dir,
            'exists': True,
            'total_files': len(files)
        })
        
    except Exception as e:
        return jsonify({'error': f'Error getting files: {str(e)}'}), 500

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