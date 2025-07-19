import os
import glob
import subprocess
from datetime import datetime

def merge_hourly_videos(video_root_path, date_str, camera_guid, hour_str=None):
    """
    Merge 1-minute segments into hourly videos for a specific camera and date.
    
    Args:
        video_root_path: Root video path (e.g., "E:/bala/installer/videos")
        date_str: Date in YYYY-MM-DD format (e.g., "2025-06-27")
        camera_guid: Camera GUID (e.g., "550e8400-e29b-41d4-a716-446655440002")
        hour_str: Optional specific hour to process (e.g., "17"), processes all hours if None
    """
    try:
        # Construct the full MP4 directory path
        mp4_dir = os.path.join(video_root_path, date_str, camera_guid, "mp4")
        
        # Verify directory exists
        if not os.path.exists(mp4_dir):
            print(f"Directory not found: {mp4_dir}")
            return False

        # Determine which hours to process
        if hour_str:
            # Process specific hour
            hours_to_process = [hour_str]
        else:
            # Process all hours found in the directory
            all_segments = glob.glob(os.path.join(mp4_dir, "????.mp4"))
            hours_to_process = sorted({f[:2] for f in (os.path.basename(p) for p in all_segments) if f[:2].isdigit()})
            
            if not hours_to_process:
                print(f"No segment files found in {mp4_dir}")
                return False

        # Process each hour
        for current_hour in hours_to_process:
            # Find all segments for this hour (e.g., 1700.mp4, 1701.mp4, etc.)
            pattern = os.path.join(mp4_dir, f"{current_hour}??.mp4")
            segment_files = glob.glob(pattern)
            
            if not segment_files:
                print(f"No segments found for hour {current_hour} in {mp4_dir}")
                continue

            # Sort files numerically
            segment_files.sort()

            # Output file path (e.g., 17.mp4)
            output_file = os.path.join(mp4_dir, f"{current_hour}.mp4")

            # Skip if output exists and is newer than all segments
            if os.path.exists(output_file):
                output_mtime = os.path.getmtime(output_file)
                newest_segment = max(os.path.getmtime(f) for f in segment_files)
                if output_mtime > newest_segment:
                    print(f"Skipping hour {current_hour} - merged file already up-to-date")
                    continue

            # Create temporary file list for ffmpeg
            file_list_path = os.path.join(mp4_dir, f"filelist_{current_hour}.txt")
            with open(file_list_path, 'w') as f:
                for seg in segment_files:
                    f.write(f"file '{os.path.basename(seg)}'\n")

            # FFmpeg command (no re-encoding)
            ffmpeg_cmd = [
                'ffmpeg',
                '-f', 'concat',
                '-safe', '0',
                '-i', file_list_path,
                '-c', 'copy',          # Stream copy
                '-movflags', 'faststart',  # Enable streaming
                output_file,
                '-y'                   # Overwrite if exists
            ]

            print(f"Merging {len(segment_files)} segments for hour {current_hour}...")
            result = subprocess.run(ffmpeg_cmd, cwd=mp4_dir, capture_output=True, text=True)

            if result.returncode != 0:
                print(f"Merge failed for hour {current_hour}: {result.stderr}")
                continue

            # Verify output file
            if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
                print(f"Error: Output file not created or empty: {output_file}")
                continue

            print(f"Successfully created: {output_file}")

            # Delete original segments
            deleted_count = 0
            for seg in segment_files:
                try:
                    os.remove(seg)
                    deleted_count += 1
                except Exception as e:
                    print(f"Warning: Could not delete {seg}: {e}")

            print(f"Deleted {deleted_count}/{len(segment_files)} segments")

            # Cleanup temporary file
            try:
                os.remove(file_list_path)
            except Exception as e:
                print(f"Warning: Could not delete {file_list_path}: {e}")

        return True

    except Exception as e:
        print(f"Error: {e}")
        return False

# Example usage for your specific path:
if __name__ == "__main__":
    # For merging all hours on 2025-06-27:
    merge_hourly_videos(
        video_root_path="E:/bala/installer/videos",
        date_str="2025-06-27",
        camera_guid="550e8400-e29b-41d4-a716-446655440002"
    )
    
    # For merging just hour 17:
    # merge_hourly_videos(..., hour_str="17")