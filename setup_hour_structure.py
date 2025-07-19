import os
from datetime import datetime

def setup_hour_structure():
    """Set up hour-based folder structure for video organization"""
    
    base_path = "videos"
    
    # Create base videos directory if it doesn't exist
    os.makedirs(base_path, exist_ok=True)
    
    # Get current date
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    # Example camera GUIDs (you can modify these based on your actual cameras)
    camera_guids = [
        "550e8400-e29b-41d4-a716-446655440001",
        "550e8400-e29b-41d4-a716-446655440002"
    ]
    
    print(f"Setting up hour-based folder structure for date: {current_date}")
    
    for camera_guid in camera_guids:
        print(f"\n📹 Setting up structure for camera: {camera_guid}")
        
        # Create camera directory
        camera_path = os.path.join(base_path, current_date, camera_guid)
        os.makedirs(camera_path, exist_ok=True)
        
        # Create hour directories (00-23)
        for hour in range(24):
            hour_str = f"{hour:02d}"
            hour_path = os.path.join(camera_path, hour_str)
            os.makedirs(hour_path, exist_ok=True)
            print(f"  ✅ Created hour folder: {hour_str}")
    
    print(f"\n🎉 Hour-based folder structure created successfully!")
    print(f"\n📁 Structure created:")
    print(f"videos/")
    print(f"  {current_date}/")
    for camera_guid in camera_guids:
        print(f"    {camera_guid}/")
        print(f"      00/  (hour 00)")
        print(f"      01/  (hour 01)")
        print(f"      ...")
        print(f"      23/  (hour 23)")
        print(f"        *.mp4 files will be stored here")

def create_example_structure():
    """Create an example structure with some sample files"""
    
    base_path = "videos"
    current_date = datetime.now().strftime("%Y-%m-%d")
    camera_guid = "550e8400-e29b-41d4-a716-446655440001"
    
    # Create the structure
    setup_hour_structure()
    
    # Create some example files to demonstrate the structure
    example_hours = ["19", "20", "21"]
    
    for hour in example_hours:
        hour_path = os.path.join(base_path, current_date, camera_guid, hour)
        
        # Create example MP4 files (empty files for demonstration)
        for minute in range(0, 60, 15):  # Every 15 minutes
            filename = f"{hour}{minute:02d}.mp4"
            file_path = os.path.join(hour_path, filename)
            
            # Create an empty file as a placeholder
            with open(file_path, 'w') as f:
                f.write(f"# Example MP4 file for {hour}:{minute:02d}")
            
            print(f"📄 Created example file: {filename} in hour {hour}")
    
    print(f"\n📋 Example structure created with sample files!")
    print(f"📁 Check: videos/{current_date}/{camera_guid}/")

if __name__ == "__main__":
    print("=== Setting up Hour-Based Video Structure ===\n")
    
    # Set up the basic structure
    setup_hour_structure()
    
    # Create example structure with sample files
    print("\n" + "="*50)
    print("Creating example structure with sample files...")
    create_example_structure()
    
    print(f"\n✅ Ready for video recording with hour-based organization!")
    print(f"📝 Your main.py will now create MP4 files in the appropriate hour folders.")
    print(f"📁 Example path: videos/{datetime.now().strftime('%Y-%m-%d')}/camera-guid/hour/*.mp4") 