import subprocess
import glob
import os
from google.cloud import speech

def extract_audio_wav(mp4_path):
    wav_path = mp4_path.rsplit(".", 1)[0] + ".wav"
    cmd = ["ffmpeg", "-y", "-i", mp4_path, "-ac", "1", "-ar", "16000", "-f", "wav", wav_path]
    subprocess.check_call(cmd)
    return wav_path

def split_wav_ffmpeg(wav_path, segment_seconds=55):
    base = wav_path.rsplit(".", 1)[0]
    out_pattern = f"{base}_part_%03d.wav"
    cmd = ["ffmpeg", "-y", "-i", wav_path, "-f", "segment", "-segment_time", str(segment_seconds), "-c", "copy", out_pattern]
    subprocess.check_call(cmd)
    return sorted(glob.glob(f"{base}_part_*.wav"))

def transcribe_wav_chunked(wav_path, language_code="pt-BR"):
    client = speech.SpeechClient()
    parts = split_wav_ffmpeg(wav_path)
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=16000,
        language_code=language_code,
        enable_automatic_punctuation=True,
        model="latest_long",
    )
    transcripts = []
    for part in parts:
        with open(part, "rb") as f:
            content = f.read()
        resp = client.recognize(config=config, audio=speech.RecognitionAudio(content=content))
        text = " ".join([r.alternatives[0].transcript for r in resp.results]).strip()
        if text: transcripts.append(text)
    return "\n".join(transcripts)