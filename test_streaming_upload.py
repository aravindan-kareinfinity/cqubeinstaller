import requests
import os
import subprocess

def test_streaming_upload():
    video_file_path = 'videos/2025-09-20/4f61f145-0835-49ab-b2cf-98e28923650c/17/1817.mp4'
    organization_id = '18'
    camera_guid = '4f61f145-0835-49ab-b2cf-98e28923650c'
    api_url = 'http://192.168.1.3:9000/api/video/upload'

    print(f'📤 Testing streaming upload of: {video_file_path}')
    print(f'📤 File size: {os.path.getsize(video_file_path)} bytes')

    with open(video_file_path, 'rb') as f:
        files = {
            'video_file': (os.path.basename(video_file_path), f, 'video/mp4')
        }
        data = {
            'organization_id': organization_id,
            'guid': camera_guid
        }
        
        print('📤 Uploading to server...')
        response = requests.post(api_url, files=files, data=data)
        
        print(f'📤 Response status: {response.status_code}')
        print(f'📤 Response content: {response.text}')
        
        if response.status_code == 200:
            print('✅ Upload successful!')
            result = response.json()
            server_file_path = result['file_path']
            print(f'✅ Server file path: {server_file_path}')
            print(f'✅ Server file size: {result["file_size"]} bytes')
            
            # Verify server file
            if os.path.exists(server_file_path):
                server_size = os.path.getsize(server_file_path)
                print(f'✅ Server file exists, size: {server_size} bytes')
                
                # Test if server file is playable
                result = subprocess.run(['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', server_file_path], 
                                      capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    print('✅ Server file is playable!')
                    # Parse and show duration
                    import json
                    probe_data = json.loads(result.stdout)
                    duration = probe_data['format']['duration']
                    print(f'✅ Video duration: {duration} seconds')
                else:
                    print('❌ Server file is not playable!')
                    print(f'❌ FFprobe error: {result.stderr}')
            else:
                print('❌ Server file not found!')
        else:
            print('❌ Upload failed!')

if __name__ == "__main__":
    test_streaming_upload()
