import os
import soundfile as sf
from misaki import zh
from kokoro_onnx import Kokoro
import warnings

warnings.filterwarnings("ignore", category=SyntaxWarning)

# 配置
MODEL_FILE = "./assets/kokoro-v1.1-zh.onnx"
VOICE_FILE = "./assets/voices-v1.1-zh.bin"
CONFIG_FILE = "./config/config.json"
OUTPUT_DIR = "audio_assets"

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

class TTSBuilder:
    def __init__(self):
        print("正在加载引擎...")
        self.g2p = zh.ZHG2P(version="1.1")
        self.kokoro = Kokoro(MODEL_FILE, VOICE_FILE, vocab_config=CONFIG_FILE)
        
    def generate(self, text, filename, speed=1.0):
        print(f"正在合成: {text} -> {filename}")
        phonemes, _ = self.g2p(text)
        samples, sample_rate = self.kokoro.create(
            phonemes, voice="zf_001", speed=speed, is_phonemes=True
        )
        path = os.path.join(OUTPUT_DIR, filename)
        sf.write(path, samples, sample_rate)
        print(f"保存成功: {path}")

if __name__ == "__main__":
    builder = TTSBuilder()
    
    # 在这里定义你需要的所有语音
    tasks = [
        ("welcome", "欢迎使用瑞芯微边缘计算语音助手。"),
        ("status_ok", "当前系统运行正常。"),
        ("warning", "警告，检测到异常。")
    ]
    
    for name, text in tasks:
        builder.generate(text, f"{name}.wav")