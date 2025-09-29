import requests
import os

# Test video upload
video_path = "videos/2025-09-20/4f61f145-0835-49ab-b2cf-98e28923650c/17/1701.mp4"

if os.path.exists(video_path):
    print(f"📤 Testing upload of: {video_path}")
    print(f"📤 File size: {os.path.getsize(video_path)} bytes")
    
    # Prepare the upload
    with open(video_path, 'rb') as f:
        files = {
            'video_file': ('1701.mp4', f, 'video/mp4')
        }
        data = {
            'organization_id': '18',
            'guid': '4f61f145-0835-49ab-b2cf-98e28923650c'
        }
        
        print("📤 Uploading to server...")
        response = requests.post('https://vms.cqubepro.com/api/video/upload', files=files, data=data)
        
        print(f"📤 Response status: {response.status_code}")
        print(f"📤 Response content: {response.text}")
        
        if response.status_code == 200:
            print("✅ Upload successful!")
            result = response.json()
            print(f"✅ Server file path: {result.get('file_path')}")
            print(f"✅ Server file size: {result.get('file_size')} bytes")
            
            # Check if the uploaded file is valid
            server_file = result.get('file_path')
            if server_file and os.path.exists(server_file):
                server_size = os.path.getsize(server_file)
                print(f"✅ Server file exists, size: {server_size} bytes")
                
                # Test if the server file is playable
                import subprocess
                try:
                    result = subprocess.run(['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', server_file], 
                                          capture_output=True, text=True, timeout=10)
                    if result.returncode == 0:
                        print("✅ Server file is playable!")
                    else:
                        print("❌ Server file is NOT playable!")
                        print(f"❌ FFprobe error: {result.stderr}")
                except Exception as e:
                    print(f"❌ Error testing server file: {e}")
            else:
                print("❌ Server file not found!")
        else:
            print("❌ Upload failed!")
else:
    print(f"❌ Video file not found: {video_path}")
