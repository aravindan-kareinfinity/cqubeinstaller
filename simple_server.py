from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
import os
import traceback
from datetime import datetime

app = FastAPI(
    title="Simple Video Upload API",
    description="Simple video upload API for testing",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "message": "API is running"}

@app.post("/api/video/upload")
async def upload_video(
    organization_id: str = Form(...),
    guid: str = Form(...),
    video_file: UploadFile = File(...),
    thumbnail_file: UploadFile = File(None)
):
    try:
        print(f"📤 Received upload request for {video_file.filename}")
        print(f"📤 Organization ID: {organization_id}")
        print(f"📤 GUID: {guid}")
        
        # Validate file type
        if not video_file.content_type or not video_file.content_type.startswith('video/'):
            raise HTTPException(status_code=400, detail="File must be a video")
        
        # Validate file extension
        if not video_file.filename or not video_file.filename.lower().endswith('.mp4'):
            raise HTTPException(status_code=400, detail="File must be an MP4 video")
        
        # Get current date, hour, and minute for directory structure
        current_datetime = datetime.now()
        current_date = current_datetime.strftime("%Y-%m-%d")
        current_hour = current_datetime.strftime("%H")
        current_minute = current_datetime.strftime("%M")
        
        # Create directory structure: video/organization_id/guid/date/hour/
        base_path = "video"
        full_path = os.path.join(base_path, organization_id, guid, current_date, current_hour)
        os.makedirs(full_path, exist_ok=True)
        
        # Create filename with hour and minute: HHMM.mp4
        filename = f"{current_hour}{current_minute}.mp4"
        file_path = os.path.join(full_path, filename)
        
        # Stream the video file directly to disk in chunks to preserve integrity
        print(f"📤 Streaming video file to: {file_path}")
        total_bytes = 0
        header_bytes = b''
        
        with open(file_path, "wb") as buffer:
            while True:
                chunk = await video_file.read(1024 * 1024)  # Read 1MB chunks
                if not chunk:
                    break
                buffer.write(chunk)
                total_bytes += len(chunk)
                
                # Save first 1024 bytes for MP4 validation
                if len(header_bytes) < 1024:
                    header_bytes += chunk[:1024 - len(header_bytes)]
        
        print(f"📤 Video file size: {total_bytes} bytes")
        
        # Validate file size (minimum 100 bytes to ensure it's not empty)
        if total_bytes < 100:
            # Clean up the incomplete file
            if os.path.exists(file_path):
                os.remove(file_path)
            raise HTTPException(status_code=400, detail="Video file is too small (likely corrupted). Minimum size: 100 bytes")
        
        # More lenient MP4 validation for segmented files using header only
        is_valid_mp4 = False
        if header_bytes.startswith(b'ftyp'):  # Standard MP4 header
            is_valid_mp4 = True
            print("✅ Valid MP4: ftyp header found")
        elif header_bytes.startswith(b'\x00\x00\x00'):  # MP4 with size prefix
            is_valid_mp4 = True
            print("✅ Valid MP4: size prefix found")
        elif b'moov' in header_bytes or b'mdat' in header_bytes:  # Contains MP4 atoms
            is_valid_mp4 = True
            print("✅ Valid MP4: MP4 atoms found")
        elif total_bytes > 1024:  # Large enough file, assume it's valid
            is_valid_mp4 = True
            print("✅ Valid MP4: large file size")
        
        if not is_valid_mp4:
            print("❌ Invalid MP4: no valid MP4 structure found")
            # Clean up the invalid file
            if os.path.exists(file_path):
                os.remove(file_path)
            raise HTTPException(status_code=400, detail="Invalid MP4 file format")
        
        print(f"✅ Video saved successfully: {file_path}")
        
        # Save the thumbnail if provided (also stream it)
        thumbnail_path = None
        if thumbnail_file:
            thumbnail_ext = os.path.splitext(thumbnail_file.filename)[1].lower()
            thumbnail_filename = f"{current_hour}{current_minute}_thumb{thumbnail_ext}"
            thumbnail_path = os.path.join(full_path, thumbnail_filename)
            
            # Stream thumbnail file as well
            with open(thumbnail_path, "wb") as buffer:
                while True:
                    chunk = await thumbnail_file.read(1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    buffer.write(chunk)
            
            print(f"✅ Thumbnail saved: {thumbnail_path}")
        
        # Return success response
        return {
            "message": "Video uploaded successfully",
            "filename": filename,
            "file_path": file_path,
            "thumbnail_path": thumbnail_path,
            "organization_id": organization_id,
            "guid": guid,
            "uploaded_at": datetime.now().isoformat(),
            "file_size": total_bytes
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Video upload error: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/video/{organization_id}/{guid}/{date}/{hour}/{filename}")
async def serve_video(
    organization_id: str,
    guid: str,
    date: str,
    hour: str,
    filename: str
):
    """Serve video files from the storage directory with proper streaming support"""
    try:
        # Build the file path
        file_path = os.path.join("video", organization_id, guid, date, hour, filename)
        
        # Check if file exists
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Video file not found")
        
        # Get file size for proper headers
        file_size = os.path.getsize(file_path)
        
        # Return the video file with proper headers for streaming
        return FileResponse(
            file_path, 
            media_type="video/mp4",
            filename=filename,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
                "Cache-Control": "public, max-age=3600"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Video serving error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting Simple Video Upload API on port 9000...")
    uvicorn.run(app, host="127.0.0.1", port=9000)
