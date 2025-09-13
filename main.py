import json
import threading
import time
import subprocess
import os
import re
import uuid
import requests
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

def camera_thread_function(camera_id, camera_name, camera_url, camera_guid):
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
            "-segment_format", "mp4",
            "-reset_timestamps", "1",
            "-strftime", "1",
            "-avoid_negative_ts", "make_zero",
            os.path.join(output_dir, "%H%M.mp4")
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
                    "-segment_format", "mp4",
                    "-reset_timestamps", "1",
                    "-strftime", "1",
                    "-avoid_negative_ts", "make_zero",
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