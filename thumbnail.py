import os
import subprocess

def generate_thumbnail(mp4_path, thumbnail_path=None, quality=2):
    """
    Creates a thumbnail from an MP4 video file
    
    Args:
        mp4_path (str): Full path to the MP4 file
        thumbnail_path (str, optional): Custom path for thumbnail. 
                                      If None, will be created in same directory as video.
        quality (int): Thumbnail quality (1-31, lower is better)
    
    Returns:
        str: Path to created thumbnail or None if failed
    """
    try:
        # Validate input path
        if not os.path.exists(mp4_path):
            print(f"Error: MP4 file not found at {mp4_path}")
            return None
        
        # Set default thumbnail path if not provided
        if thumbnail_path is None:
            video_dir = os.path.dirname(mp4_path)
            video_name = os.path.splitext(os.path.basename(mp4_path))[0]
            thumbnail_path = os.path.join(video_dir, f"{video_name}_thumb.jpg")
        
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
    

video_path = r"./videos\2025-07-19\bf971549-38b8-4fa4-85d8-beb2aede9b87\16\1638.mp4"
thumbnail_path = create_thumbnail_for_mp4(video_path)

if thumbnail_path:
    print(f"Thumbnail created at: {thumbnail_path}")
else:
    print("Failed to create thumbnail")