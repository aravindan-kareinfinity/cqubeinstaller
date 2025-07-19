import os
import glob
from main import generate_thumbnail

def generate_missing_thumbnails():
    """Generate thumbnails for all videos that don't have thumbnails"""
    base_path = "./videos"
    
    if not os.path.exists(base_path):
        print("Videos directory not found")
        return
    
    # Find all video directories
    for date_dir in os.listdir(base_path):
        date_path = os.path.join(base_path, date_dir)
        if not os.path.isdir(date_path):
            continue
            
        print(f"Processing date: {date_dir}")
        
        # Find all camera directories
        for camera_dir in os.listdir(date_path):
            camera_path = os.path.join(date_path, camera_dir)
            if not os.path.isdir(camera_path):
                continue
                
            print(f"  Processing camera: {camera_dir}")
            
            # Find all hour directories
            for hour_dir in os.listdir(camera_path):
                hour_path = os.path.join(camera_path, hour_dir)
                if not os.path.isdir(hour_path):
                    continue
                    
                print(f"    Processing hour: {hour_dir}")
                
                # Find all MP4 files
                mp4_files = glob.glob(os.path.join(hour_path, "*.mp4"))
                
                for mp4_file in mp4_files:
                    video_name = os.path.splitext(os.path.basename(mp4_file))[0]
                    thumbnail_path = os.path.join(hour_path, f"{video_name}_thumb.jpg")
                    
                    # Check if thumbnail exists
                    if not os.path.exists(thumbnail_path):
                        print(f"      Generating thumbnail for: {os.path.basename(mp4_file)}")
                        result = generate_thumbnail(mp4_file, thumbnail_path)
                        if result:
                            print(f"      ✅ Created: {os.path.basename(thumbnail_path)}")
                        else:
                            print(f"      ❌ Failed: {os.path.basename(mp4_file)}")
                    else:
                        print(f"      ✓ Thumbnail exists: {os.path.basename(thumbnail_path)}")

if __name__ == "__main__":
    print("Generating missing thumbnails for all videos...")
    generate_missing_thumbnails()
    print("Done!") 