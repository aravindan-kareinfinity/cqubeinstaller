import json
from pathlib import Path
import threading
import time
import subprocess
import os
import cv2
import glob
import queue
import requests
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_from_directory, Response
import urllib

app = Flask(__name__)

# Global variable to store camera data
cameras_data = []
# Global variable to store base video path
base_video_path = ""
# Dictionary to store active threads for each camera
active_threads = {}
# Dictionary to store stop flags for each camera
stop_flags = {}
# Dictionary to store merging tasks for each camera
merging_tasks = {}
# Global merge queue and worker thread
merge_queue = queue.Queue()
merge_worker_thread = None
merge_worker_stop_flag = False
# Global variable for API configuration
api_organization_id = 14  # Default to match your config.json organization ID


import os

def mp4_to_bytes(file_path):
    """
    Convert an MP4 file to bytes
    
    Args:
        file_path (str): Path to the MP4 file
        
    Returns:
        bytes: The MP4 file content as bytes or None if failed
    """
    try:
        # Validate file exists
        if not os.path.exists(file_path):
            print(f"Error: File not found at {file_path}")
            return None
            
        # Validate it's an MP4 file
        if not file_path.lower().endswith('.mp4'):
            print("Error: File is not an MP4")
            return None
            
        # Read file as bytes
        with open(file_path, 'rb') as file:
            file_bytes = file.read()
            
        print(f"Successfully converted MP4 to bytes (size: {len(file_bytes)} bytes)")
        return file_bytes
        
    except Exception as e:
        print(f"Error converting MP4 to bytes: {str(e)}")
        return None
        
        
def generate_thumbnail(mp4_path, thumbnail_path=None, quality=2):

    try:
        # Validate input path
        if not os.path.exists(mp4_path):
            print(f"Error: MP4 file not found at {mp4_path}")
            return None
        print(f"E found at {mp4_path}")
        byteshj = mp4_to_bytes(mp4_path)
        
        
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
        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        
        # Verify thumbnail was created
        if os.path.exists(thumbnail_path) and os.path.getsize(thumbnail_path) > 0:
            print(f"Successfully created thumbnail at {thumbnail_path}")
            return thumbnail_path
        
        # If first attempt failed, try getting first frame
        print("First attempt failed, trying to get first frame...")
        ffmpeg_cmd[4] = "00:00:00"  # Seek to start
        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        
        if os.path.exists(thumbnail_path) and os.path.getsize(thumbnail_path) > 0:
            print(f"Successfully created thumbnail at {thumbnail_path}")
            return thumbnail_path
        
        print(f"Failed to create thumbnail: {result.stderr}")
        return None
        
    except Exception as e:
        print(f"Error creating thumbnail: {e}")
        return None
    

def upload_video_to_api(video_file_path, organization_id=None, camera_guid=None):
    """Upload video to API and always attempt thumbnail generation"""
    try:
        api_url = "http://127.0.0.1:8000/api/video/upload"
        
        # Check if file exists (don't check size)
        if not os.path.exists(video_file_path):
            print(f"❌ Error: Video file not found: {video_file_path}")
            return False
        
        # Always attempt thumbnail generation
        video_dir = os.path.dirname(video_file_path)
        video_name = os.path.splitext(os.path.basename(video_file_path))[0]
        thumbnail_path = os.path.join(video_dir, f"{video_name}.jpg")
        
        print(f"🔄 Generating thumbnail for: {os.path.basename(video_file_path)}")
        generated_thumbnail_path = generate_thumbnail(video_file_path, thumbnail_path)
        
        # Retry thumbnail generation if first attempt failed
        if not generated_thumbnail_path:
            print(f"🔄 Retrying thumbnail generation for: {os.path.basename(video_file_path)}")
            generated_thumbnail_path = generate_thumbnail(video_file_path, thumbnail_path)
        
        # Prepare files for upload - always include video
        files = {
            'video_file': (os.path.basename(video_file_path), open(video_file_path, 'rb'), 'video/mp4')
        }
        
        # Add thumbnail if generated successfully
        if generated_thumbnail_path and os.path.exists(generated_thumbnail_path):
            try:
                files['thumbnail_file'] = (os.path.basename(generated_thumbnail_path), open(generated_thumbnail_path, 'rb'), 'image/jpeg')
                print(f"✅ Thumbnail included: {os.path.basename(generated_thumbnail_path)}")
            except Exception as e:
                print(f"❌ Error adding thumbnail: {e}")
        else:
            print(f"⚠️ No thumbnail available for: {os.path.basename(video_file_path)}")
        
        data = {
            'organization_id': str(organization_id or api_organization_id),
        }
        if camera_guid:
            data['guid'] = camera_guid
        
        print(f"📤 Uploading {len(files)} files to API:")
        for file_key, file_info in files.items():
            print(f"  - {file_key}: {file_info[0]} ({file_info[2]})")
        
        # Upload with timeout
        response = requests.post(api_url, files=files, data=data, timeout=300)
        
        # Close all file handles
        for file_info in files.values():
            file_info[1].close()
        
        if response.status_code == 200:
            print(f"✅ Upload successful: {os.path.basename(video_file_path)} with {len(files)} files")
            return True
        else:
            print(f"❌ Upload failed (HTTP {response.status_code}): {os.path.basename(video_file_path)}")
            print(f"Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error uploading video: {e}")
        return False


def merge_worker_thread_function():
    """Global worker thread that processes all merge operations from the queue"""
    global merge_worker_stop_flag
    print("Merge worker thread started")
    
    while not merge_worker_stop_flag:
        try:
            # Get merge task from queue with timeout
            try:
                task = merge_queue.get(timeout=1)  # 1 second timeout
            except queue.Empty:
                continue  # Continue loop if no tasks
            
            if task is None:  # Shutdown signal
                print("Received shutdown signal, stopping merge worker thread")
                break
                
            camera_guid, hour_str, task_type = task
            
            print(f"Processing merge task: {task_type} for camera {camera_guid}, hour {hour_str} (Queue size: {merge_queue.qsize()})")
            
            if task_type == "existing":
                # Handle existing segments merge
                try:
                    current_date = datetime.now().strftime("%Y-%m-%d")
                    segment_dir = os.path.join(base_video_path, current_date, camera_guid, "mp4")
                    if not os.path.exists(segment_dir):
                        print(f"No segment directory found for camera {camera_guid}")
                        continue
                    
                    # Check if 1-minute segments exist for this hour
                    pattern = os.path.join(segment_dir, f"{hour_str}*.mp4")
                    segment_files = glob.glob(pattern)
                    
                    if len(segment_files) >= 2:
                        print(f"Merging existing 1-minute segments for hour {hour_str} in camera {camera_guid}")
                        # Call the actual merge function
                        merge_hourly_segments_direct(camera_guid, hour_str)
                    else:
                        print(f"Not enough segments to merge for hour {hour_str} in camera {camera_guid}")
                        
                except Exception as e:
                    print(f"Error in existing merge task for hour {hour_str} in camera {camera_guid}: {e}")
            
            elif task_type == "hourly":
                # Handle hourly merge (from start_hourly_merging)
                try:
                    print(f"Processing hourly merge for hour {hour_str} in camera {camera_guid}")
                    merge_hourly_segments_direct(camera_guid, hour_str)
                except Exception as e:
                    print(f"Error in hourly merge task for hour {hour_str} in camera {camera_guid}: {e}")
            
            elif task_type == "manual":
                # Handle manual merge (from API trigger)
                try:
                    print(f"Processing manual merge for hour {hour_str} in camera {camera_guid}")
                    merge_hourly_segments_direct(camera_guid, hour_str)
                except Exception as e:
                    print(f"Error in manual merge task for hour {hour_str} in camera {camera_guid}: {e}")
            
            # Mark task as done
            merge_queue.task_done()
            print(f"Completed merge task: {task_type} for camera {camera_guid}, hour {hour_str}")
            
        except Exception as e:
            print(f"Error in merge worker thread: {e}")
            time.sleep(1)  # Wait before continuing
    
    print("Merge worker thread stopped")

def start_merge_worker_thread():
    """Start the global merge worker thread"""
    global merge_worker_thread, merge_worker_stop_flag
    
    if merge_worker_thread is None or not merge_worker_thread.is_alive():
        merge_worker_stop_flag = False
        merge_worker_thread = threading.Thread(target=merge_worker_thread_function, name="merge_worker")
        merge_worker_thread.daemon = True
        merge_worker_thread.start()
        print("Started global merge worker thread")

def stop_merge_worker_thread():
    """Stop the global merge worker thread"""
    global merge_worker_thread, merge_worker_stop_flag
    
    if merge_worker_thread and merge_worker_thread.is_alive():
        merge_worker_stop_flag = True
        # Send shutdown signal
        merge_queue.put(None)
        merge_worker_thread.join(timeout=10)
        print("Stopped global merge worker thread")

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
                capture_stills = False  # Set to True if you want to capture still images
                stills_interval = 60    # Capture one still image every X seconds
                
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
                        capture_stills = settings.get('capture_stills', capture_stills)
                        stills_interval = settings.get('stills_interval', stills_interval)
                        
                        # Handle resolution
                        res = settings.get('resolution', '1280x720')
                        if 'x' in res:
                            width, height = res.split('x')
                            resolution = f"{width}:{height}"
                

                
                # Create separate FFmpeg processes for video and image capture
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
                
                # Image capture command (separate process)
                image_ffmpeg_cmd = [
                    "ffmpeg",
                    "-rtsp_transport", "tcp",
                    "-i", camera_rtsp_url,
                    "-vf", f"scale={resolution},fps=1/{stills_interval}",
                    "-f", "image2",
                    "-strftime", "1",
                    os.path.join(output_dir, "%H%M.jpg")
                ]

                print(f"Running video ffmpeg command: {' '.join(video_ffmpeg_cmd)}")
                print(f"Running image ffmpeg command: {' '.join(image_ffmpeg_cmd)}")
                
                # Start video ffmpeg process
                video_process = subprocess.Popen(
                    video_ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    bufsize=1
                )
                
                # Start image ffmpeg process
                image_process = subprocess.Popen(
                    image_ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    bufsize=1
                )
                
                print(f"Video FFmpeg process started with PID: {video_process.pid}")
                print(f"Image FFmpeg process started with PID: {image_process.pid}")
                
                # Monitor FFmpeg output in real-time
                def monitor_video_ffmpeg():
                    while video_process.poll() is None:
                        line = video_process.stderr.readline()
                        if line:
                            print(f"Video FFmpeg [{camera_name}]: {line.strip()}")
                
                def monitor_image_ffmpeg():
                    while image_process.poll() is None:
                        line = image_process.stderr.readline()
                        if line:
                            print(f"Image FFmpeg [{camera_name}]: {line.strip()}")
                
                # Start monitoring threads
                video_monitor_thread = threading.Thread(target=monitor_video_ffmpeg)
                video_monitor_thread.daemon = True
                video_monitor_thread.start()
                
                image_monitor_thread = threading.Thread(target=monitor_image_ffmpeg)
                image_monitor_thread.daemon = True
                image_monitor_thread.start()
                
                # Monitor file creation and generate thumbnails during video creation
                def monitor_files():
                    last_files = set()
                    processing_files = set()
                    while (video_process.poll() is None or image_process.poll() is None) and not stop_flags.get(camera_id, False):
                        try:
                            current_files = set()
                            if os.path.exists(output_dir):
                                for file in os.listdir(output_dir):
                                    if file.endswith('.mp4') or file.endswith('.jpg'):
                                        current_files.add(file)
                            
                            # Check for new files
                            new_files = current_files - last_files
                            if new_files:
                                for new_file in new_files:
                                    print(f"New file detected [{camera_name}]: {new_file}")
                                    processing_files.add(new_file)
                                    file_path = os.path.join(output_dir, new_file)
                                    print(f"New video/image file detected [{camera_name}]: {new_file}")
                            
                            # Check for completed files
                            completed_files = set()
                            for file in processing_files:
                                file_path = os.path.join(output_dir, file)
                                if os.path.exists(file_path):
                                    try:
                                        size1 = os.path.getsize(file_path)
                                        time.sleep(1)
                                        size2 = os.path.getsize(file_path)
                                        if size1 == size2:
                                            completed_files.add(file)
                                    except:
                                        pass
                            
                            # Process completed files for upload
                            for completed_file in completed_files:
                                if completed_file in processing_files:
                                    processing_files.remove(completed_file)
                                
                                print(f"File completed [{camera_name}]: {completed_file}")
                                file_path = os.path.join(output_dir, completed_file)
                                
                                # Determine if it's a video or image and upload accordingly
                                if completed_file.endswith('.mp4'):
                                    def upload_segment():
                                        try:
                                            upload_video_to_api(file_path, camera_guid=camera_guid)
                                        except Exception as e:
                                            print(f"Upload error: {completed_file} - {e}")
                                    upload_thread = threading.Thread(target=upload_segment, 
                                                                  name=f"upload_{camera_name}_{completed_file}")
                                    upload_thread.daemon = True
                                    upload_thread.start()
                              
                            
                            last_files = current_files
                            time.sleep(2)
                        except Exception as e:
                            print(f"Error monitoring files for {camera_name}: {e}")
                            time.sleep(5)
                
                # Start file monitoring thread
                file_monitor_thread = threading.Thread(target=monitor_files)
                file_monitor_thread.daemon = True
                file_monitor_thread.start()
            
                # Wait for processes to complete or stop flag to be set
                while (video_process.poll() is None or image_process.poll() is None) and not stop_flags.get(camera_id, False):
                    time.sleep(1)
                
                # If stop flag is set, terminate the processes
                if stop_flags.get(camera_id, False):
                    print(f"Stopping recording for camera: {camera_name} ({camera_id})")
                    
                    # Terminate video process
                    if video_process.poll() is None:
                        video_process.terminate()
                        try:
                            video_process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            video_process.kill()
                    
                    # Terminate image process
                    if image_process.poll() is None:
                        image_process.terminate()
                        try:
                            image_process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            image_process.kill()
                    
                    break
                
                # If processes ended naturally, log it and restart
                if (video_process.poll() is not None or image_process.poll() is not None) and not stop_flags.get(camera_id, False):
                    print(f"Recording process ended unexpectedly for camera: {camera_name} ({camera_id})")
                    
                    # Check video process
                    if video_process.poll() is not None:
                        stdout, stderr = video_process.communicate()
                        if stderr:
                            print(f"Video FFmpeg error output: {stderr}")
                        if stdout:
                            print(f"Video FFmpeg output: {stdout}")
                    
                    # Check image process
                    if image_process.poll() is not None:
                        stdout, stderr = image_process.communicate()
                        if stderr:
                            print(f"Image FFmpeg error output: {stderr}")
                        if stdout:
                            print(f"Image FFmpeg output: {stdout}")
                    
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
    
    # Try to get RTSP URL from camera config - use 'rtsp_url' field
    camera_rtsp_url = camera.get('rtsp_url', '')
    camera_guid = camera.get('guid', str(camera_id))  # Get GUID from camera config, fallback to ID as string
    
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
        
        # Stop merging task if it exists
        camera_guid = None
        for cam in cameras_data:
            if cam['id'] == camera_id:
                camera_guid = cam.get('guid', camera_id)
                break
        
        if camera_guid and camera_guid in merging_tasks:
            # Set a flag to stop the merging thread (we'll add this to the merging worker)
            # For now, we'll just remove it from the dictionary
            del merging_tasks[camera_guid]
        
        # Remove from active threads and clean up
        del active_threads[camera_id]
        if camera_id in stop_flags:
            del stop_flags[camera_id]
        
        print(f"Stopped recording thread for camera ID: {camera_id}")

def merge_existing_segments():
    """Merge any existing segments from previous hours when system starts"""
    try:
        current_date = datetime.now().strftime("%Y-%m-%d")
        current_hour = datetime.now().strftime("%H")
        
        # Get all camera GUIDs
        camera_guids = set()
        for camera in cameras_data:
            camera_guid = camera.get('guid', camera['id'])
            camera_guids.add(camera_guid)
        
        # Add merge tasks to the global queue
        for camera_guid in camera_guids:
            segment_dir = os.path.join(base_video_path, current_date, camera_guid, "mp4")
            if not os.path.exists(segment_dir):
                continue
            
            # Check for any incomplete hours (hours before current hour)
            for hour in range(24):
                hour_str = f"{hour:02d}"
                if hour_str >= current_hour:
                    continue  # Skip current and future hours
                
                # Check if 1-minute segments exist for this hour
                pattern = os.path.join(segment_dir, f"{hour_str}*.mp4")
                segment_files = glob.glob(pattern)
                
                if len(segment_files) >= 2:
                    # Add merge task to the global queue
                    merge_queue.put((camera_guid, hour_str, "existing"))
                    print(f"Added existing merge task to queue for hour {hour_str} in camera {camera_guid}")
        
        print(f"Added {merge_queue.qsize()} existing merge tasks to queue")
            
    except Exception as e:
        print(f"Error in merge_existing_segments: {e}")

def load_cameras():
    """Load cameras from config.json"""
    global cameras_data, base_video_path, api_organization_id
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        cameras_data = config.get('cameras', [])
        
        # Try to get base_video_path from config, if not found use default
        base_video_path = config.get('base_video_path', './videos')
        
        # If still not found, try to get from organization settings
        if base_video_path == './videos':
            org = config.get('organization', {})
            if org and 'server_video_path' in org:
                # Extract local path from server_video_path if it's a local path
                server_path = org['server_video_path']
                if not server_path.startswith(('http://', 'https://', 'firebase://')):
                    base_video_path = server_path
                else:
                    # For cloud storage, use local videos directory
                    base_video_path = './videos'
        
        # Also check the root level server_video_path
        if base_video_path == './videos':
            root_server_path = config.get('server_video_path')
            if root_server_path and not root_server_path.startswith(('http://', 'https://', 'firebase://')):
                base_video_path = root_server_path
        
        # Get organization ID from config
        org = config.get('organization', {})
        if org and 'id' in org:
            api_organization_id = org['id']
            print(f"Organization ID loaded from config: {api_organization_id}")
        
        # Create base video directory if it doesn't exist
        os.makedirs(base_video_path, exist_ok=True)
        
        print(f"Loaded {len(cameras_data)} cameras from config.json")
        print(f"Base video path: {base_video_path}")
        print(f"Organization ID: {api_organization_id}")
        
        # Start the global merge worker thread
        start_merge_worker_thread()
        
        # Start recording for cameras that were already recording
        for camera in cameras_data:
            if camera.get('is_recording', False):
                start_camera_thread(camera)
                
        # Merge existing segments
        merge_existing_segments()
        
        # Generate thumbnails for existing videos
        generate_missing_thumbnails()
        
        # Clean up any orphaned segments
        cleanup_all_orphaned_segments()
        
    except FileNotFoundError:
        print("config.json not found. Creating default config...")
        cameras_data = []
        base_video_path = './videos'
        os.makedirs(base_video_path, exist_ok=True)
        
        # Create default config
        default_config = {
            'base_video_path': base_video_path,
            'cameras': []
        }
        
        with open('config.json', 'w') as f:
            json.dump(default_config, f, indent=2)
            
    except Exception as e:
        print(f"Error loading cameras: {e}")
        cameras_data = []
        base_video_path = './videos'

def save_cameras():
    """Save cameras back to config.json"""
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        config['cameras'] = cameras_data
        
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

@app.route('/api/cameras/<camera_id>/toggle', methods=['POST'])
def toggle_camera_recording(camera_id):
    """API endpoint to toggle camera recording status"""
    global cameras_data
    
    # Convert camera_id to int for comparison since your config uses numeric IDs
    try:
        camera_id_int = int(camera_id)
    except ValueError:
        return jsonify({'error': 'Invalid camera ID format'}), 400
    
    # Find the camera
    camera = None
    for cam in cameras_data:
        if cam['id'] == camera_id_int:
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
        stop_camera_thread(camera_id_int)
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
    """API endpoint to get camera HTTP URL for live viewing"""
    # Convert camera_id to int for comparison since your config uses numeric IDs
    try:
        camera_id_int = int(camera_id)
    except ValueError:
        return jsonify({'error': 'Invalid camera ID format'}), 400
    
    # Find the camera
    camera = None
    for cam in cameras_data:
        if cam['id'] == camera_id_int:
            camera = cam
            break
    
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404
    
    # Generate HTTP URL for live viewing
    live_url = get_camera_live_url(camera_id_int)
    
    # Get RTSP URL for reference (used for recording)
    camera_rtsp_url = camera.get('rtsp_url', '')
    
    return jsonify({
        'camera_id': camera_id,
        'camera_name': camera['name'],
        'live_url': live_url,
        'rtsp_url': camera_rtsp_url,
        'status': 'live' if live_url else 'no_url'
    })

@app.route('/api/cameras/<camera_id>/stream')
def stream_camera(camera_id):
    """HTTP streaming endpoint - returns HTTP URL for browser streaming"""
    # Find the camera
    camera = None
    for cam in cameras_data:
        if cam['id'] == camera_id:
            camera = cam
            break
    
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404
    
    # Generate HTTP URL for live viewing
    live_url = get_camera_live_url(camera_id)
    
    # Get RTSP URL for reference (used for recording)
    camera_rtsp_url = camera.get('rtsp_url', '')
    
    return jsonify({
        'camera_id': camera_id,
        'camera_name': camera['name'],
        'live_url': live_url,
        'rtsp_url': camera_rtsp_url,
        'stream_type': 'http_stream'
    })

@app.route('/api/cameras/<camera_id>/files')
def get_camera_files(camera_id):
    """API endpoint to get current video files for a camera"""
    # Convert camera_id to int for comparison since your config uses numeric IDs
    try:
        camera_id_int = int(camera_id)
    except ValueError:
        return jsonify({'error': 'Invalid camera ID format'}), 400
    
    # Find the camera
    camera = None
    for cam in cameras_data:
        if cam['id'] == camera_id_int:
            camera = cam
            break
    
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404
    
    try:
        camera_guid = camera.get('guid', str(camera_id_int))
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

@app.route('/api/cameras/<camera_id>/frame-status')
def get_frame_status(camera_id):
    """API endpoint to get camera RTSP status for debugging"""
    # Find the camera
    camera = None
    for cam in cameras_data:
        if cam['id'] == camera_id:
            camera = cam
            break
    
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404
    
    try:
        is_recording = camera.get('is_recording', False)
        camera_url = camera.get('rtsp_url', '')
        
        return jsonify({
            'camera_id': camera_id,
            'name': camera['name'],
            'rtsp_url': camera_url,
            'is_recording': is_recording,
            'has_active_thread': camera_id in active_threads,
            'status': 'active' if is_recording and camera_url else 'inactive',
            'message': 'Camera ready for direct RTSP streaming' if camera_url else 'No RTSP URL configured'
        })
        
    except Exception as e:
        return jsonify({'error': f'Error getting frame status: {str(e)}'}), 500

@app.route('/api/threads/validate')
def validate_all_threads():
    """API endpoint to validate all camera threads"""
    validation_results = {}
    
    for camera in cameras_data:
        camera_id = camera['id']
        is_recording = camera.get('is_recording', False)
        has_active_thread = camera_id in active_threads
        
        status = "consistent"
        issues = []
        
        if is_recording:
            if not has_active_thread:
                status = "error"
                issues.append("Missing active thread")
        else:
            if has_active_thread:
                status = "warning"
                issues.append("Extra active thread")
        
        validation_results[camera_id] = {
            'camera_name': camera['name'],
            'is_recording': is_recording,
            'has_active_thread': has_active_thread,
            'status': status,
            'issues': issues
        }
    
    return jsonify({
        'validation_results': validation_results,
        'total_cameras': len(cameras_data),
        'consistent_cameras': sum(1 for r in validation_results.values() if r['status'] == 'consistent'),
        'error_cameras': sum(1 for r in validation_results.values() if r['status'] == 'error'),
        'warning_cameras': sum(1 for r in validation_results.values() if r['status'] == 'warning')
    })

@app.route('/api/cameras/<camera_id>/merge/<hour_str>', methods=['POST'])
def trigger_merge(camera_id, hour_str):
    """API endpoint to manually trigger merging for a specific hour"""
    # Find the camera
    camera = None
    for cam in cameras_data:
        if cam['id'] == camera_id:
            camera = cam
            break
    
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404
    
    try:
        camera_guid = camera.get('guid', camera_id)
        
        # Add merge task to the global queue
        merge_queue.put((camera_guid, hour_str, "manual"))
        
        return jsonify({
            'success': True,
            'message': f'Merge task added to queue for hour {hour_str} in camera {camera_id}',
            'camera_name': camera['name'],
            'queue_size': merge_queue.qsize()
        })
        
    except Exception as e:
        return jsonify({'error': f'Error triggering merge: {str(e)}'}), 500

def merge_hourly_segments_direct(camera_guid, hour_str):
    """Direct merge function that performs the actual merging without creating a new thread"""
    try:
        # Get the date for today
        current_date = datetime.now().strftime("%Y-%m-%d")
        segment_dir = os.path.join(base_video_path, current_date, camera_guid, "mp4")
        
        # Find all 1-minute segment files for this hour (e.g., 1600.mp4, 1601.mp4, ..., 1659.mp4)
        pattern = os.path.join(segment_dir, f"{hour_str}*.mp4")
        segment_files = glob.glob(pattern)
        
        if len(segment_files) < 2:
            print(f"Not enough segments to merge for hour {hour_str} in camera {camera_guid}")
            return
        
        # Sort files by name to ensure correct order
        segment_files.sort()
        
        # Create output filename (e.g., 16.mp4)
        output_file = os.path.join(segment_dir, f"{hour_str}.mp4")
        
        # Skip if output exists and is newer than all segments
        if os.path.exists(output_file):
            output_mtime = os.path.getmtime(output_file)
            newest_segment = max(os.path.getmtime(f) for f in segment_files)
            if output_mtime > newest_segment:
                print(f"Skipping hour {hour_str} - merged file already up-to-date")
                return
        
        # Build ffmpeg command using concat demuxer with direct file input
        # Use a different approach that doesn't require text files
        ffmpeg_cmd = [
            "ffmpeg",
            "-i", "concat:" + "|".join([os.path.basename(f) for f in segment_files]),
            "-c", "copy",          # Stream copy (no re-encoding)
            "-movflags", "faststart",  # Enable streaming
            f"{hour_str}.mp4",     # Output filename (relative)
            "-y"                   # Overwrite output file if it exists
        ]
        
        print(f"Merging {len(segment_files)} 1-minute segments for hour {hour_str} in camera {camera_guid}")
        print(f"Command: {' '.join(ffmpeg_cmd)}")
        
        # Run ffmpeg merge from the segment directory
        result = subprocess.run(ffmpeg_cmd, cwd=segment_dir, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"Successfully merged 1-minute segments into 1-hour video: {output_file}")
            
            # Verify the merged file was created successfully
            if os.path.exists(output_file):
                file_size = os.path.getsize(output_file)
                print(f"1-hour video created successfully: {output_file} (Size: {file_size} bytes)")
                
                # Upload the merged video to API
                print(f"Starting upload of merged video: {output_file}")
                upload_success = upload_video_to_api(output_file, camera_guid=camera_guid)
                
                if upload_success:
                    print(f"Video upload completed successfully for hour {hour_str} in camera {camera_guid}")
                else:
                    print(f"Video upload failed for hour {hour_str} in camera {camera_guid}")
                
                # Delete original 1-minute segment files
                deleted_count = 0
                for segment_file in segment_files:
                    try:
                        if os.path.exists(segment_file):
                            os.remove(segment_file)
                            deleted_count += 1
                            print(f"Deleted 1-minute segment: {os.path.basename(segment_file)}")
                        else:
                            print(f"Warning: Segment file not found: {segment_file}")
                    except Exception as e:
                        print(f"Error deleting segment {segment_file}: {e}")
                
                print(f"Deleted {deleted_count} out of {len(segment_files)} 1-minute segments")
                
                # Verify deletion by checking remaining files
                remaining_segments = glob.glob(pattern)
                if remaining_segments:
                    print(f"Warning: {len(remaining_segments)} 1-minute segments still exist after deletion")
                    for remaining in remaining_segments:
                        print(f"  - {os.path.basename(remaining)}")
                else:
                    print(f"All 1-minute segments for hour {hour_str} successfully deleted")
                
            else:
                print(f"Error: 1-hour video file was not created: {output_file}")
                return
                
        else:
            print(f"Error merging segments for hour {hour_str}: {result.stderr}")
            # Don't delete segments if merge failed
            return
            
    except Exception as e:
        print(f"Error in merge_hourly_segments_direct for hour {hour_str} in camera {camera_guid}: {e}")

def merge_hourly_segments(camera_guid, hour_str):
    """Merge all video segments for a specific hour into a single file"""
    # Add merge task to the global queue
    merge_queue.put((camera_guid, hour_str, "hourly"))
    print(f"Added hourly merge task to queue for hour {hour_str} in camera {camera_guid}")

def cleanup_orphaned_segments(camera_guid):
    """Clean up any orphaned 1-minute segments that should have been deleted"""
    def cleanup_worker():
        try:
            current_date = datetime.now().strftime("%Y-%m-%d")
            segment_dir = os.path.join(base_video_path, current_date, camera_guid, "mp4")
            
            if not os.path.exists(segment_dir):
                print(f"No segment directory found for camera {camera_guid}")
                return
            
            # Find all 1-minute segment files (format: HHMM.mp4)
            all_segments = []
            for file in os.listdir(segment_dir):
                if file.endswith('.mp4') and len(file) == 8:  # HHMM.mp4 format
                    all_segments.append(file)
            
            if not all_segments:
                print(f"No 1-minute segments found for camera {camera_guid}")
                return
            
            # Group segments by hour
            segments_by_hour = {}
            for segment in all_segments:
                hour = segment[:2]  # Extract hour from filename
                if hour not in segments_by_hour:
                    segments_by_hour[hour] = []
                segments_by_hour[hour].append(segment)
            
            # Check each hour
            for hour, segments in segments_by_hour.items():
                hour_file = os.path.join(segment_dir, f"{hour}.mp4")
                
                # If 1-hour file exists, delete all 1-minute segments for that hour
                if os.path.exists(hour_file):
                    print(f"1-hour file exists for hour {hour}, cleaning up {len(segments)} 1-minute segments")
                    deleted_count = 0
                    
                    for segment in segments:
                        segment_path = os.path.join(segment_dir, segment)
                        try:
                            if os.path.exists(segment_path):
                                os.remove(segment_path)
                                deleted_count += 1
                                print(f"Deleted orphaned segment: {segment}")
                        except Exception as e:
                            print(f"Error deleting orphaned segment {segment}: {e}")
                    
                    print(f"Cleaned up {deleted_count} orphaned segments for hour {hour}")
                else:
                    print(f"No 1-hour file for hour {hour}, keeping {len(segments)} 1-minute segments")
                    
        except Exception as e:
            print(f"Error in cleanup worker for camera {camera_guid}: {e}")
        finally:
            print(f"Cleanup worker for camera {camera_guid} completed")
    
    # Create and start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_worker, name=f"cleanup_{camera_guid}")
    cleanup_thread.daemon = True
    cleanup_thread.start()
    print(f"Started cleanup thread for camera {camera_guid}")

@app.route('/api/cameras/<camera_id>/cleanup', methods=['POST'])
def trigger_cleanup(camera_id):
    """API endpoint to manually trigger cleanup of orphaned segments"""
    # Find the camera
    camera = None
    for cam in cameras_data:
        if cam['id'] == camera_id:
            camera = cam
            break
    
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404
    
    try:
        camera_guid = camera.get('guid', camera_id)
        
        # Trigger cleanup in individual thread
        cleanup_orphaned_segments(camera_guid)
        
        return jsonify({
            'success': True,
            'message': f'Cleanup triggered for camera {camera_id}',
            'camera_name': camera['name']
        })
        
    except Exception as e:
        return jsonify({'error': f'Error triggering cleanup: {str(e)}'}), 500

@app.route('/api/cameras/<camera_id>/files/status')
def get_camera_files_status(camera_id):
    """API endpoint to get detailed file status showing segments and merged files"""
    # Find the camera
    camera = None
    for cam in cameras_data:
        if cam['id'] == camera_id:
            camera = cam
            break
    
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404
    
    try:
        camera_guid = camera.get('guid', camera_id)
        current_date = datetime.now().strftime("%Y-%m-%d")
        segment_dir = os.path.join(base_video_path, current_date, camera_guid, "mp4")
        
        if not os.path.exists(segment_dir):
            return jsonify({
                'camera_id': camera_id,
                'camera_name': camera['name'],
                'directory': segment_dir,
                'exists': False,
                'segments': [],
                'hourly_files': [],
                'orphaned_segments': []
            })
        
        # Get all files in the directory
        all_files = []
        for file in os.listdir(segment_dir):
            if file.endswith('.mp4'):
                file_path = os.path.join(segment_dir, file)
                file_stat = os.stat(file_path)
                all_files.append({
                    'name': file,
                    'size': file_stat.st_size,
                    'modified': datetime.fromtimestamp(file_stat.st_mtime).isoformat(),
                    'path': file_path
                })
        
        # Categorize files
        segments = []  # 1-minute segments (HHMM.mp4 format)
        hourly_files = []  # 1-hour files (HH.mp4 format)
        
        for file_info in all_files:
            filename = file_info['name']
            if len(filename) == 8:  # HHMM.mp4 format
                segments.append(file_info)
            elif len(filename) == 6:  # HH.mp4 format
                hourly_files.append(file_info)
        
        # Find orphaned segments (segments that have corresponding hourly files)
        orphaned_segments = []
        for segment in segments:
            hour = segment['name'][:2]  # Extract hour
            # Check if corresponding hourly file exists
            hourly_file_exists = any(hf['name'] == f"{hour}.mp4" for hf in hourly_files)
            if hourly_file_exists:
                orphaned_segments.append(segment)
        
        # Sort files
        segments.sort(key=lambda x: x['name'])
        hourly_files.sort(key=lambda x: x['name'])
        orphaned_segments.sort(key=lambda x: x['name'])
        
        return jsonify({
            'camera_id': camera_id,
            'camera_name': camera['name'],
            'directory': segment_dir,
            'exists': True,
            'segments': segments,
            'hourly_files': hourly_files,
            'orphaned_segments': orphaned_segments,
            'total_segments': len(segments),
            'total_hourly_files': len(hourly_files),
            'total_orphaned': len(orphaned_segments),
            'total_size_segments': sum(s['size'] for s in segments),
            'total_size_hourly': sum(h['size'] for h in hourly_files)
        })
        
    except Exception as e:
        return jsonify({'error': f'Error getting file status: {str(e)}'}), 500

@app.route('/api/merge/queue/status')
def get_merge_queue_status():
    """API endpoint to get merge queue status"""
    try:
        return jsonify({
            'queue_size': merge_queue.qsize(),
            'worker_thread_alive': merge_worker_thread.is_alive() if merge_worker_thread else False,
            'worker_thread_name': merge_worker_thread.name if merge_worker_thread else None,
            'stop_flag': merge_worker_stop_flag
        })
    except Exception as e:
        return jsonify({'error': f'Error getting merge queue status: {str(e)}'}), 500

@app.route('/api/config/organization_id', methods=['GET', 'POST'])
def manage_organization_id():
    """API endpoint to get and set the organization_id for video uploads"""
    global api_organization_id
    
    if request.method == 'GET':
        return jsonify({
            'organization_id': api_organization_id,
            'message': 'Current organization_id for video uploads'
        })
    
    elif request.method == 'POST':
        try:
            data = request.get_json()
            new_organization_id = data.get('organization_id')
            
            if new_organization_id is None:
                return jsonify({'error': 'organization_id is required'}), 400
            
            if not isinstance(new_organization_id, int):
                return jsonify({'error': 'organization_id must be an integer'}), 400
            
            api_organization_id = new_organization_id
            
            return jsonify({
                'success': True,
                'organization_id': api_organization_id,
                'message': f'Organization ID updated to {api_organization_id}'
            })
            
        except Exception as e:
            return jsonify({'error': f'Error updating organization_id: {str(e)}'}), 500

@app.route('/api/video/upload/<camera_id>/<hour_str>', methods=['POST'])
def trigger_video_upload(camera_id, hour_str):
    """API endpoint to manually trigger video upload for a specific hour"""
    # Convert camera_id to int for comparison since your config uses numeric IDs
    try:
        camera_id_int = int(camera_id)
    except ValueError:
        return jsonify({'error': 'Invalid camera ID format'}), 400
    
    # Find the camera
    camera = None
    for cam in cameras_data:
        if cam['id'] == camera_id_int:
            camera = cam
            break
    
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404
    
    try:
        camera_guid = camera.get('guid', str(camera_id_int))
        current_date = datetime.now().strftime("%Y-%m-%d")
        video_file_path = os.path.join(base_video_path, current_date, camera_guid, "mp4", f"{hour_str}.mp4")
        
        if not os.path.exists(video_file_path):
            return jsonify({
                'error': f'Video file not found: {video_file_path}',
                'camera_name': camera['name'],
                'file_path': video_file_path
            }), 404
        
        # Upload the video (now includes thumbnail generation)
        upload_success = upload_video_to_api(video_file_path, camera_guid=camera_guid)
        
        if upload_success:
            return jsonify({
                'success': True,
                'message': f'Video uploaded successfully for hour {hour_str} in camera {camera_id}',
                'camera_name': camera['name'],
                'file_path': video_file_path,
                'organization_id': api_organization_id,
                'thumbnail_generated': True
            })
        else:
            return jsonify({
                'error': f'Failed to upload video for hour {hour_str} in camera {camera_id}',
                'camera_name': camera['name'],
                'file_path': video_file_path
            }), 500
        
    except Exception as e:
        return jsonify({'error': f'Error uploading video: {str(e)}'}), 500

@app.route('/api/video/upload/status')
def get_upload_status():
    """API endpoint to get video upload configuration and status"""
    try:
        return jsonify({
            'api_url': 'http://127.0.0.1:8000/api/video/upload',
            'organization_id': api_organization_id,
            'upload_enabled': True,
            'timeout_seconds': 300,
            'upload_types': {
                '1_minute_segments': 'Automatically uploaded when created',
                'hourly_videos': 'Automatically uploaded after merging'
            },
            'message': 'Both 1-minute segments and hourly videos are automatically uploaded'
        })
    except Exception as e:
        return jsonify({'error': f'Error getting upload status: {str(e)}'}), 500

@app.route('/api/video/upload/segment/<camera_id>/<filename>', methods=['POST'])
def trigger_segment_upload(camera_id, filename):
    """API endpoint to manually trigger upload of a specific 1-minute video segment"""
    # Convert camera_id to int for comparison since your config uses numeric IDs
    try:
        camera_id_int = int(camera_id)
    except ValueError:
        return jsonify({'error': 'Invalid camera ID format'}), 400
    
    # Find the camera
    camera = None
    for cam in cameras_data:
        if cam['id'] == camera_id_int:
            camera = cam
            break
    
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404
    
    try:
        camera_guid = camera.get('guid', str(camera_id_int))
        current_date = datetime.now().strftime("%Y-%m-%d")
        video_file_path = os.path.join(base_video_path, current_date, camera_guid, "mp4", filename)
        
        if not os.path.exists(video_file_path):
            return jsonify({
                'error': f'Video segment file not found: {video_file_path}',
                'camera_name': camera['name'],
                'file_path': video_file_path
            }), 404
        
        # Validate filename format (should be HHMM.mp4)
        if not (filename.endswith('.mp4') and len(filename) == 8 and filename[:4].isdigit()):
            return jsonify({
                'error': f'Invalid filename format. Expected HHMM.mp4 format, got: {filename}',
                'camera_name': camera['name']
            }), 400
        
        # Upload the video segment (now includes thumbnail generation)
        upload_success = upload_video_to_api(video_file_path, camera_guid=camera_guid)
        
        if upload_success:
            return jsonify({
                'success': True,
                'message': f'1-minute video segment uploaded successfully: {filename}',
                'camera_name': camera['name'],
                'file_path': video_file_path,
                'organization_id': api_organization_id,
                'segment_type': '1-minute',
                'thumbnail_generated': True
            })
        else:
            return jsonify({
                'error': f'Failed to upload 1-minute video segment: {filename}',
                'camera_name': camera['name'],
                'file_path': video_file_path
            }), 500
        
    except Exception as e:
        return jsonify({'error': f'Error uploading video segment: {str(e)}'}), 500

@app.route('/api/video/segments/<camera_id>')
def get_camera_segments(camera_id):
    """API endpoint to get all 1-minute video segments for a camera"""
    # Find the camera
    camera = None
    for cam in cameras_data:
        if cam['id'] == camera_id:
            camera = cam
            break
    
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404
    
    try:
        camera_guid = camera.get('guid', camera_id)
        current_date = datetime.now().strftime("%Y-%m-%d")
        segment_dir = os.path.join(base_video_path, current_date, camera_guid, "mp4")
        
        if not os.path.exists(segment_dir):
            return jsonify({
                'camera_id': camera_id,
                'camera_name': camera['name'],
                'directory': segment_dir,
                'exists': False,
                'segments': [],
                'total_segments': 0
            })
        
        # Get all 1-minute segment files (format: HHMM.mp4)
        segments = []
        for file in os.listdir(segment_dir):
            if file.endswith('.mp4') and len(file) == 8:  # HHMM.mp4 format
                file_path = os.path.join(segment_dir, file)
                file_stat = os.stat(file_path)
                
                # Check if this is a 1-minute segment (not an hourly file)
                if file[:4].isdigit():  # HHMM format
                    segments.append({
                        'filename': file,
                        'size': file_stat.st_size,
                        'modified': datetime.fromtimestamp(file_stat.st_mtime).isoformat(),
                        'path': file_path,
                        'hour': file[:2],
                        'minute': file[2:4],
                        'segment_type': '1-minute'
                    })
        
        # Sort segments by filename (chronological order)
        segments.sort(key=lambda x: x['filename'])
        
        return jsonify({
            'camera_id': camera_id,
            'camera_name': camera['name'],
            'directory': segment_dir,
            'exists': True,
            'segments': segments,
            'total_segments': len(segments),
            'total_size': sum(s['size'] for s in segments)
        })
        
    except Exception as e:
        return jsonify({'error': f'Error getting camera segments: {str(e)}'}), 500


@app.route('/api/images/<camera_id>')
def get_camera_images(camera_id):
    """API endpoint to get all image files for a camera"""
    # Find the camera
    camera = None
    for cam in cameras_data:
        if cam['id'] == camera_id:
            camera = cam
            break
    
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404
    
    try:
        camera_guid = camera.get('guid', camera_id)
        current_date = datetime.now().strftime("%Y-%m-%d")
        segment_dir = os.path.join(base_video_path, current_date, camera_guid, "mp4")
        
        if not os.path.exists(segment_dir):
            return jsonify({
                'camera_id': camera_id,
                'camera_name': camera['name'],
                'directory': segment_dir,
                'exists': False,
                'images': [],
                'total_images': 0
            })
        
        # Get all image files (format: HHMM.jpg)
        images = []
        for file in os.listdir(segment_dir):
            if file.endswith('.jpg') and len(file) == 8:  # HHMM.jpg format
                file_path = os.path.join(segment_dir, file)
                file_stat = os.stat(file_path)
                
                # Check if this is a still image (HHMM format)
                if file[:4].isdigit():  # HHMM format
                    images.append({
                        'filename': file,
                        'size': file_stat.st_size,
                        'modified': datetime.fromtimestamp(file_stat.st_mtime).isoformat(),
                        'path': file_path,
                        'hour': file[:2],
                        'minute': file[2:4],
                        'image_type': 'still'
                    })
        
        # Sort images by filename (chronological order)
        images.sort(key=lambda x: x['filename'])
        
        return jsonify({
            'camera_id': camera_id,
            'camera_name': camera['name'],
            'directory': segment_dir,
            'exists': True,
            'images': images,
            'total_images': len(images),
            'total_size': sum(i['size'] for i in images)
        })
        
    except Exception as e:
        return jsonify({'error': f'Error getting camera images: {str(e)}'}), 500

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
                
                # Merge segments for the completed hour
                merge_hourly_segments(camera_guid, completed_hour)
                
            except Exception as e:
                print(f"Error in merging worker for camera {camera_guid}: {e}")
                time.sleep(60)  # Wait a minute before retrying
    
    # Start merging thread
    merging_thread = threading.Thread(target=merging_worker)
    merging_thread.daemon = True
    merging_tasks[camera_guid] = merging_thread
    merging_thread.start()
    print(f"Started hourly merging task for camera {camera_guid}")

def generate_missing_thumbnails():
    """Generate thumbnails for any existing videos that don't have thumbnails"""
    def thumbnail_worker():
        try:
            current_date = datetime.now().strftime("%Y-%m-%d")
            segment_dir = os.path.join(base_video_path, current_date)
            
            if not os.path.exists(segment_dir):
                print(f"No segment directory found for date {current_date}")
                return
            
            # Find all camera directories
            camera_dirs = [d for d in os.listdir(segment_dir) if os.path.isdir(os.path.join(segment_dir, d))]
            
            if not camera_dirs:
                print(f"No camera directories found for date {current_date}")
                return
            
            # Process each camera
            for camera_dir in camera_dirs:
                camera_guid = camera_dir
                mp4_dir = os.path.join(segment_dir, camera_guid, "mp4")
                
                if not os.path.exists(mp4_dir):
                    continue
                
                # Find all MP4 files without thumbnails
                for file in os.listdir(mp4_dir):
                    if file.endswith('.mp4'):
                        video_path = os.path.join(mp4_dir, file)
                        video_name = os.path.splitext(file)[0]
                        thumbnail_path = os.path.join(mp4_dir, f"{video_name}_thumb.jpg")
                        
                        # Generate thumbnail if it doesn't exist
                        if not os.path.exists(thumbnail_path):
                            print(f"Generating missing thumbnail for: {file}")
                            try:
                                generate_thumbnail(video_path, thumbnail_path)
                                print(f"✅ Generated thumbnail: {os.path.basename(thumbnail_path)}")
                            except Exception as e:
                                print(f"❌ Failed to generate thumbnail for {file}: {e}")
                
        except Exception as e:
            print(f"Error in generate_missing_thumbnails: {e}")
        finally:
            print(f"Thumbnail generation worker completed")
    
    # Create and start thumbnail generation thread
    thumbnail_thread = threading.Thread(target=thumbnail_worker, name="generate_missing_thumbnails")
    thumbnail_thread.daemon = True
    thumbnail_thread.start()
    print(f"Started thumbnail generation thread for existing videos")

def cleanup_all_orphaned_segments():
    """Clean up any orphaned segments for all cameras"""
    def cleanup_worker():
        try:
            current_date = datetime.now().strftime("%Y-%m-%d")
            segment_dir = os.path.join(base_video_path, current_date)
            
            if not os.path.exists(segment_dir):
                print(f"No segment directory found for date {current_date}")
                return
            
            # Find all camera directories
            camera_dirs = [d for d in os.listdir(segment_dir) if os.path.isdir(os.path.join(segment_dir, d))]
            
            if not camera_dirs:
                print(f"No camera directories found for date {current_date}")
                return
            
            # Clean up each camera
            for camera_dir in camera_dirs:
                camera_guid = camera_dir
                cleanup_orphaned_segments(camera_guid)
                
        except Exception as e:
            print(f"Error in cleanup_all_orphaned_segments: {e}")
        finally:
            print(f"Cleanup worker for all cameras completed")
    
    # Create and start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_worker, name="cleanup_all_orphaned_segments")
    cleanup_thread.daemon = True
    cleanup_thread.start()
    print(f"Started cleanup thread for all cameras")
def run_mediamtx():
    """Run MediaMTX with robust path handling"""
    script_dir = Path(__file__).parent
    
    # Check possible locations (add more if needed)
    possible_paths = [
        script_dir / "mediamtx.exe",               # Same directory
        script_dir / "mediamtx" / "mediamtx.exe",  # Subdirectory
        Path("E:/bala/installer/mediamtx.exe"),   # Absolute path
        Path("E:/bala/installer/mediamtx/mediamtx.exe")
    ]
    
    # Find the first valid path
    mediamtx_path = None
    for path in possible_paths:
        if path.exists():
            mediamtx_path = path
            break
    
    if not mediamtx_path:
        raise FileNotFoundError(
            "MediaMTX executable not found. Tried:\n" +
            "\n".join(f"- {p}" for p in possible_paths)
        )

    # Config file path (same directory as executable)
    config_path = mediamtx_path.parent / "mediamtx.yml"
    
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found at {config_path}")

    try:
        print(f"Starting MediaMTX from: {mediamtx_path}")
        process = subprocess.Popen(
            [str(mediamtx_path), str(config_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=mediamtx_path.parent  # Run from mediamtx's directory
        )
        print(f"✅ MediaMTX started (PID: {process.pid})")
        return process
    except Exception as e:
        print(f"❌ Failed to start MediaMTX: {str(e)}")
        raise


def generate_mediamtx_config(cameras_data):
    """Generate MediaMTX configuration from camera data."""
    config = """# Auto-generated MediaMTX configuration
# https://github.com/bluenviron/mediamtx

paths:
"""
    
    for camera in cameras_data['cameras']:
        # Clean camera name for path
        path_name = camera['name'].strip().replace(' ', '_').lower()
        
        # Get RTSP URL from 'rtsp_url' field
        rtsp_url = camera.get('rtsp_url')
        
        if not rtsp_url:
            print(f"⚠️ Skipping camera '{camera['name']}' - no RTSP URL found")
            continue
            
        # Decode URL-encoded characters
        decoded_url = urllib.parse.unquote(rtsp_url)
        
        # Add camera configuration
        config += f"""  {path_name}:
    source: "{decoded_url}"
 
"""
    
    # Add default configuration
    config += """  all_others:
    source: discard
    sourceOnDemand: true
"""
    return config

def get_camera_live_url(camera_id):
    """Generate HTTP URL for live viewing based on camera ID"""
    # Generate HTTP URL pattern: http://localhost/cam1, cam2, etc.
    live_url = f"http://localhost/cam{camera_id}"
    
    return live_url

def load_camera_data(file_path):
    """Load camera data from JSON file with error handling."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # Validate required fields
        if 'cameras' not in data:
            raise ValueError("JSON missing 'cameras' array")
            
        return data
        
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON format: {e}")
        return None
    except Exception as e:
        print(f"❌ Error loading camera data: {e}")
        return None
    

def main():
    """Main function"""
    # Load cameras first
    load_cameras()
    config_path = Path("config.json")
    output_path = Path("mediamtx/mediamtx.yml")
    
    # Load camera data from config.json
    print(f"📂 Loading camera data from: {config_path}")
    cameras_data = load_camera_data(config_path)
    
    if not cameras_data:
        print("❌ No valid camera data loaded - exiting")
        return
    
    # Generate the MediaMTX configuration
    config_content = generate_mediamtx_config(cameras_data)
    
    # Start the Flask server
    print("Starting Camera Management System...")
    print(f"Organization ID: {api_organization_id}")
    print("Web interface available at: http://localhost:5000")
    print("Press Ctrl+C to stop the server")

    try:
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(config_content)
        print(f"✅ Configuration saved to: {output_path}")
    except Exception as e:
        print(f"❌ Failed to write config file: {e}")
        return
    
    try:
        mediamtx_process = run_mediamtx()
        app.run(host='192.168.31.122', port=5000, debug=False)  # Set debug=False for production
    except KeyboardInterrupt:
        print("\nShutting down...")
        # Stop all active threads
        for camera_id in list(active_threads.keys()):
            stop_camera_thread(camera_id)
        # Stop the merge worker thread
        stop_merge_worker_thread()
        # Terminate MediaMTX process if it exists
        if 'mediamtx_process' in locals():
            mediamtx_process.terminate()
        print("All threads stopped.")
        
if __name__ == "__main__":
    main() 