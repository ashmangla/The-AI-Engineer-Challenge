import os
import tempfile
from pathlib import Path
from typing import List
import logging
from faster_whisper import WhisperModel
import yt_dlp

logger = logging.getLogger(__name__)

class VideoLoader:
    """Handles downloading and transcribing YouTube videos."""
    
    def __init__(self, url: str):
        """Initialize with YouTube URL."""
        self.url = url
        self.documents: List[str] = []
        self.model = WhisperModel("base", device="cpu", compute_type="int8")
    
    def download_audio(self) -> str:
        """Download audio from YouTube video."""
        try:
            # Create a temporary directory for the audio file
            temp_dir = tempfile.mkdtemp()
            temp_audio_path = os.path.join(temp_dir, "audio.mp4")
            
            # Use yt-dlp to download audio
            import yt_dlp
            
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': temp_audio_path,
                'quiet': True,
                'no_warnings': True,
                'extract_audio': True,
                # Add user agent and referer to avoid 403 errors
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-us,en;q=0.5',
                    'Sec-Fetch-Mode': 'navigate',
                },
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                logger.info(f"Downloading audio from: {self.url}")
                ydl.download([self.url])
            
            if not os.path.exists(temp_audio_path):
                raise ValueError("Audio file was not downloaded")
                
            return temp_audio_path
            
        except Exception as e:
            logger.error(f"Error downloading audio: {str(e)}")
            raise ValueError(f"Could not download audio from YouTube: {str(e)}")
            
        finally:
            # Log the file existence and size for debugging
            if os.path.exists(temp_audio_path):
                logger.info(f"Audio file downloaded: {temp_audio_path} ({os.path.getsize(temp_audio_path)} bytes)")
    
    def transcribe_audio(self, audio_path: str) -> None:
        """Transcribe audio file using faster-whisper."""
        try:
            # Transcribe audio using faster-whisper
            segments, info = self.model.transcribe(audio_path, beam_size=5)
            
            logger.info(f"Detected language '{info.language}' with probability {info.language_probability}")
            
            # Format transcription with timestamps
            transcription = []
            for segment in segments:
                start_time = int(segment.start)
                text = segment.text.strip()
                if text:
                    timestamp = f"[{start_time//60:02d}:{start_time%60:02d}]"
                    transcription.append(f"{timestamp} {text}")
            
            # Join segments and store in documents
            self.documents = ["\n".join(transcription)]
            
        except Exception as e:
            logger.error(f"Error transcribing audio: {str(e)}")
            raise ValueError(f"Could not transcribe audio: {str(e)}")
        
        finally:
            # Clean up audio file
            if os.path.exists(audio_path):
                os.unlink(audio_path)
                os.rmdir(os.path.dirname(audio_path))
    
    def load_url(self) -> None:
        """Download and transcribe YouTube video."""
        audio_path = self.download_audio()
        self.transcribe_audio(audio_path)
