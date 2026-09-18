#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================================
# localai_news_bot.py — AIラボ鯖 ローカルAI速報 自動配信（GitHub配布用・独立スクリプト）
# ----------------------------------------------------------------
# 方式: 朝活/激裏型。Google News検索RSS＋実証済みニュース母体を分類別クエリで切って投稿。
#       AI分類は使わず route_id / source で分類を固定（IC倶楽部方式）。
# 設計の正本: 自分/AIニュース15分類_取得クエリ設計_完全再設計版 1.md（2026-07-08）
# 2026-07-09: 初期実運用で「量は出るが質が荒い」ことを確認。
#   方針変更: 公式RSS/GitHub release/APIを主水路にしない。
#   トレンド朝活で効いているニュース母体を15分類のクエリ違いで使い回す。
#
# 使い方:
#   1) pip install feedparser
#   2) Webhookを環境変数で（GitHub Actionsは Secrets 推奨）:
#        DISCORD_LOCALAI_WEBHOOK_{OPENAI,CLAUDE,GEMINI,XAI,COPILOT,META,CHINA,
#                              LOCAL,IMGVID,AUDIO,TOOLS,PAPERS,GENERAL,RELEASE,WORLD}
#   3) ローカルテストは環境変数が無ければ同ディレクトリ localai_core_webhooks.json を fallback（配布時 .gitignore）
#   4) python localai_news_bot.py
#   5) 自動化: .github/workflows で cron '15,45 * * * *'
# 重複排除: 同ディレクトリ localai_core_seen_urls.json / 投稿POSTには User-Agent 必須(無いとCloudflare403)
# ================================================================
import os, sys, io, json, time, re, calendar, urllib.parse, urllib.request, urllib.error
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
try:
    import feedparser
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "feedparser"])
    import feedparser

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(HERE, "localai_core_seen_urls.json")
WEBHOOKS_JSON = os.path.join(HERE, "localai_core_webhooks.json")
PER_SOURCE = 3        # ★速報化: 1ソースから拾う上限
PER_CHANNEL = 6       # ★2026-09-16 盛々: 4→6(1chの1回あたり投稿上限を上げる)
FRESH_HOURS = 72      # ★2026-09-16 盛々: 48→72(時間窓を広げる・情報量アップ)
NOW = time.time()     # 実行開始時刻(UTC epoch)。時間窓判定の基準
UA = "LocalAiBot/1.0 (+https://discord.com)"
COL_BIZ, COL_FIELD, COL_SUM = 0x00E5FF, 0x00FF9C, 0xFF7A1A

GLOBAL_EXCLUDE = [
    "PR TIMES", "プレスリリース", "アットプレス", "valuepress",
    "株価", "決算", "ホールド評価", "求人", "採用", "セミナー", "イベント開催",
    "ライブ配信", "ウェビナー", "講座", "Investing.com", "ファイナンス", "金融ニュース",
    "使ってみた", "とは？", "とは何か", "徹底解説", "始め方", "初心者", "AIsmiley", "ai-market.jp",
    "キャンペーン", "無料公開", "広告", "Sponsored",
    "料金はいくら", "全プラン比較", "最適な選び方", "おすすめランキング", "資料請求",
    "日記", "使い倒し", "仕事術", "入門",
]

def rss(url, include=None, exclude=None, label=None, title_include=None, title_exclude=None):
    return {"type": "rss", "url": url, "include": include or [], "exclude": exclude or [],
            "label": label, "title_include": title_include or [], "title_exclude": title_exclude or []}

def gn(q, include=None, exclude=None, label=None, title_include=None, title_exclude=None):
    return {"type": "gn", "q": q, "include": include or [], "exclude": exclude or [],
            "label": label, "title_include": title_include or [], "title_exclude": title_exclude or []}

def sitemap(url, include=None, exclude=None, label=None, path_prefix=None, title_include=None, title_exclude=None):
    return {"type": "sitemap", "url": url, "include": include or [], "exclude": exclude or [],
            "label": label, "path_prefix": path_prefix or [], "title_include": title_include or [],
            "title_exclude": title_exclude or []}


LOCAL_TITLE_EXCLUDE = ["Course -", "Bootcamp", "Masterclass", "Udemy", "Coursera",
                       "Tutorial", "使ってみた", "とは?", "始め方", "初心者向け", "入門",
                       "求人", "採用", "セミナー"]
# is_release_version_noise() が参照する
MODEL_RELEASE_TERMS = ["新モデル", "モデル公開", "オープンウェイト", "提供開始", "generally available",
                       "open weights", "GPT", "Claude", "Gemini", "Grok", "Llama", "Qwen", "DeepSeek",
                       "Mistral", "Gemma", "Phi", "released", "release", "launch"]

COL_FW, COL_MODEL, COL_QUANT, COL_VLM, COL_STT = 0x00E5FF, 0xFFB800, 0xFF4747, 0x9C27B0, 0x2196F3

LLM_FW_TERMS = ["Hugging Face", "Ollama", "llama.cpp", "GGUF", "LM Studio", "vLLM", "SGLang",
                "ExLlama", "MLX", "llamafile", "KoboldCpp", "text-generation-webui", "TensorRT-LLM",
                "MLC LLM", "ローカルLLM", "ローカル LLM", "ローカル環境", "ローカルで動", "セルフホスト",
                "self-host", "local LLM", "run locally", "LocalLLaMA", "オンプレ", "推論エンジン",
                "tok/s", "tokens/s", "KVキャッシュ", "MoE", "on-device", "inference"]
LLM_FW_EXCLUDE = ["攻撃", "侵入", "マルウェア", "ランサム", "詐欺", "脆弱性", "情報流出", "不正アクセス",
                  "[写真]", "値下げ", "株式", "売上高", "資金調達", "買収", "上場",
                  "提携", "パートナーシップ", "訴訟", "蒸留",
                  "iPhone", "Snapdragon", "Qualcomm", "ワークステーション", "スマートフォン"]
# ★2026-09-15 実測: "Yi-"/"Falcon"/"Wizard"/"Magnum"/"Dolphin"は一般語衝突(NFL/ドラゴンの武器/映画等)→固有名詞化
LLM_MODEL_TERMS = ["Llama", "Qwen", "Gemma", "Mistral", "Mixtral", "DeepSeek", "Phi-", "Command R",
                   "Yi-6", "Yi-9", "Yi-34", "Yi-1.5", "Nemotron", "GLM-4", "GLM-5", "InternLM",
                   "Falcon LLM", "OLMo", "Kimi", "MiniMax", "Reka Core", "DBRX",
                   "Nous Hermes", "Nous Research", "Dolphin 3", "Dolphin-llama", "WizardLM",
                   "Magnum-v", "MythoMax-L2", "オープンウェイト", "open-weight", "open weights",
                   "重み公開", "ウェイト公開", "モデル公開", "モデル提供開始", "released", "launch",
                   "Hugging Face"]
LLM_MODEL_EXCLUDE = ["攻撃", "マルウェア", "脆弱性", "訴訟", "iPhone", "Snapdragon", "スマートフォン",
                    "株価", "決算", "投資", "銘柄", "NFL", "recap", "Falcons -", "Falcon Peak",
                    "Wizard review", "Falcon 9", "SpaceX", "War Wizard",
                    # ★2026-09-15 実測ノイズ: 車のカスタム"モデル"誤ヒット
                    "トヨタ", "ホンダ", "日産", "スバル", "マツダ", "スズキ",
                    "ルーミー", "GRパーツ", "GRヤリス", "カスタムモデル", "スポーティ",
                    "徳井義実", "愛車", "改良モデル", "カスタムカー", "くるまのニュース",
                    "MotorTrend", "Motor Fan", "goo-net", "福井新聞",
                    "Dragonlance", "GamingTrend",
                    # ★2026-09-17 CDP実測ノイズ: テニス試合誤ヒット
                    "テニステンプル", "ラサロ・ガルシア", "ミンテギ", "バレンシア 2026",
                    "テニス", "デル・オルモ"]
QUANT_TERMS = ["GGUF", "EXL2", "EXL3", "AWQ", "GPTQ", "MXFP4", "INT8 quant", "INT4 quant",
               "bitsandbytes", "Marlin", "TensorRT-LLM", "MLX", "SGLang", "DeepSpeed",
               "TorchAO", "HQQ", "量子化", "quantiz", "推論最適化", "inference optimization",
               "K-quant", "IQ-quant", "Q4_K", "Q5_K", "Q8_", "Q6_K", "KVキャッシュ", "kv cache",
               "speculative decoding", "投機的デコード", "vLLM", "推論高速化", "unsloth quant"]
QUANT_EXCLUDE = ["株価", "資金調達", "買収"]
# ★2026-09-15 実測: "Whisper"/"Parakeet"/"Canary"/"Reverb"/"Silero"は一般語衝突→固有名詞化
VLM_TERMS = ["Qwen-VL", "Qwen2-VL", "Qwen2.5-VL", "Qwen3-VL", "InternVL", "InternVL 2", "InternVL 3",
             "InternVL 2.5", "Molmo", "Llama-Vision", "Llama 3.2 Vision", "Pixtral", "Pixtral-Large",
             "MiniCPM-V", "MiniCPM-o", "Phi-3.5-vision", "Phi-4-vision", "Phi-4-multimodal",
             "DeepSeek-VL", "DeepSeek-VL2", "Ovis", "Aria multimodal", "Idefics", "Idefics3", "LLaVA",
             "LLaVA-OneVision", "CogVLM", "CogVLM2", "Kosmos", "Kosmos-2", "GLM-4V", "GLM-4.5V",
             "vision language model", "VLM", "multimodal LLM", "マルチモーダル", "画像理解",
             "vision model", "Cambrian", "OmniVLM", "動画理解",
             # ★2026-09-18 盛々: Hikârư指摘対応(VLM薄い→2025-2026新モデル追加)
             "SmolVLM", "SmolVLM2", "NuExtract", "Ovis2", "Ovis 2", "Molmo-72B",
             "Aya Vision", "PaliGemma", "PaliGemma 2", "Florence", "Florence-2",
             "Gemma 3", "Ministral", "Mistral Small 3"]
VLM_EXCLUDE = ["株価", "決算", "投資",
               # ★2026-09-16 CDP実測ノイズ: 医療系マルチモーダル誤ヒット
               "多発性骨髄腫", "骨髄腫", "生存予測", "予後予測", "予測モデル",
               "東大と理研", "医療研究", "臨床", "疾患予測", "腫瘍", "がん治療",
               "医療画像", "動的マルチモーダル生存"]
STT_TERMS = ["OpenAI Whisper", "whisper.cpp", "faster-whisper", "WhisperX", "distil-whisper",
             "Whisper Large v3", "Whisper v3 Turbo", "Whisper Turbo", "whisper-turbo",
             "NeMo Parakeet", "Parakeet TDT", "NeMo Canary", "Silero VAD", "Vosk speech",
             "Reverb ASR", "Reverb.ai", "SenseVoice", "音声認識", "speech recognition", "STT",
             "speech-to-text", "文字起こし", "transcription AI", "VAD model", "voice activity",
             # ★2026-09-18 盛々: 2025-2026新モデル
             "Moonshine STT", "Moonshine ONNX", "Kyutai STT", "Kyutai unmute", "Kotoba Whisper",
             "ReazonSpeech", "SpeechT5", "Nemotron-STT"]
STT_EXCLUDE = ["買収", "訴訟", "Alexa", "Google Home", "Yacht WHISPER", "Yacht Whisper",
               "Parakeets, male", "Parakeets bird", "Yacht", "Monaco",
               # ★2026-09-15 実測ノイズ: NLサッカー/映画/イベント誤ヒット
               "Tottenham", "Rodrigo", "Bentancur", "Bellingham", "Spanish GP", "LiveScore",
               "Anime News Network", "映画館", "Cinema", "Mishpacha", "Choose the Whisper",
               "Times of India", "Jantar Mantar", "attack", "casters", "The Apothecary",
               "The False", "cure of life", "shall whisper", "AuK", "Nano banana",
               # ★2026-09-16 CDP実測ノイズ: クラウド音声認識サービス広告/法人系
               "RECAIUS", "global.toshiba", "ボイストリガー",
               "音声認識ミドルウェア", "カスハラ", "コールセンター",
               "対策義務化", "オンデマンド配信", "イザ！",
               "Phys.org", "male or female", "love a good fight",
               # ★船名・映画名でWhisper誤ヒット
               "Luxury yacht", "yacht rental", "Sofascore",
               # ★2026-09-16 -/+実測で残ったノイズ: 局所ニュース/市場調査/クラウドAmiVoice
               "岐阜名鉄タクシー", "自動配車システム", "大山崎町議会", "字幕表示モニター",
               "AmiVoice", "AmiVoice Cloud Platform", "トークマウス",
               "音声認識マイクモジュール", "CAGR", "市場規模マップ", "8.1%",
               "エプソン", "Endeavor NL3000E", "文字起こしAIアプリ標準搭載",
               "産経ニュース", "news.nicovideo.jp",
               "多言語案内看板", "西条", "京都府大山崎町", "外国人観光客向け", "音声ガイダンス実証実験",
               "迷える流れ星", "マリオネットドールズ", "東京マリオネット"]

TOPICS = [
 {"num":"🧠","name":"ローカルllm速報","env":"LLM_FW","color":COL_FW,"sources":[
     rss("https://www.reddit.com/r/LocalLLaMA/hot/.rss?limit=25", include=LLM_FW_TERMS + ["LLM", "model"]),
     gn('llama.cpp OR Ollama OR "LM Studio" OR vLLM OR SGLang OR ExLlama OR "text-generation-webui" OR llamafile OR KoboldCpp OR "MLC LLM"',
        include=LLM_FW_TERMS, exclude=LLM_FW_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     gn('"ローカルLLM" OR "ローカル環境" OR "ローカルで動" OR "セルフホスト" OR "自宅サーバー" OR "オンプレLLM"',
        include=LLM_FW_TERMS, exclude=LLM_FW_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     rss("https://news.google.com/rss/search?q=%22local%20LLM%22%20OR%20%22run%20locally%22%20OR%20llama.cpp%20OR%20GGUF%20OR%20Ollama%20OR%20%22LM%20Studio%22%20OR%20vLLM%20OR%20SGLang&hl=en-US&gl=US&ceid=US:en",
         include=LLM_FW_TERMS, exclude=LLM_FW_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     gn('site:x.com "llama.cpp" OR "Ollama" OR "LM Studio" OR "GGUF" OR "vLLM" OR "SGLang"',
        include=LLM_FW_TERMS, exclude=LLM_FW_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     rss("https://zenn.dev/topics/llm/feed", include=LLM_FW_TERMS + ["ローカル", "推論"]),
     rss("https://zenn.dev/topics/ollama/feed", include=LLM_FW_TERMS + ["LLM"]),
     rss("https://zenn.dev/topics/ai/feed", include=LLM_FW_TERMS),
     rss("https://zenn.dev/topics/machinelearning/feed", include=LLM_FW_TERMS),
     rss("https://huggingface.co/blog/feed.xml", include=LLM_FW_TERMS + ["inference", "agents"]),
     rss("https://ollama.com/blog/rss.xml", include=LLM_FW_TERMS + ["model", "MLX"]),
     rss("https://lmstudio.ai/rss.xml", include=LLM_FW_TERMS + ["model"]),
     rss("https://github.com/ggerganov/llama.cpp/releases.atom", include=LLM_FW_TERMS + ["cuda", "metal", "backend", "kernel"]),
     rss("https://github.com/ollama/ollama/releases.atom", include=LLM_FW_TERMS + ["model", "support"]),
     rss("https://github.com/vllm-project/vllm/releases.atom", include=LLM_FW_TERMS + ["performance", "improve"]),
     rss("https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", include=LLM_FW_TERMS),
     # ★2026-09-16 盛々: 日本語タグ追加(実測 fresh 3-4件・強い)
     rss("https://qiita.com/tags/ollama/feed", include=LLM_FW_TERMS + ["Ollama", "LLM"]),
     rss("https://zenn.dev/topics/ollama/feed", include=LLM_FW_TERMS + ["Ollama"]),
     rss("https://zenn.dev/topics/vllm/feed", include=LLM_FW_TERMS + ["vLLM", "推論"]),
     # ★2026-09-18 v4: 激裏ACTIVE_CONFIRMED＋rss_discover実測(24h fresh 3-25件)
     rss("https://openai.com/news/rss.xml", include=LLM_FW_TERMS + ["release", "model", "API"], exclude=LLM_FW_EXCLUDE),
     rss("https://www.reddit.com/r/singularity/new/.rss?limit=25", include=LLM_FW_TERMS + ["LLM", "model", "local", "GGUF"]),
     rss("https://arstechnica.com/ai/feed/", include=LLM_FW_TERMS + ["LLM", "language model"]),
     rss("https://www.marktechpost.com/feed/", include=LLM_FW_TERMS + ["LLM", "language model"]),
     rss("https://simonwillison.net/atom/everything/", include=LLM_FW_TERMS + ["LLM", "language model"]),
     rss("https://qiita.com/tags/llm/feed", include=LLM_FW_TERMS + ["LLM"]),
     rss("https://aismiley.co.jp/ai_news/feed/", include=LLM_FW_TERMS + ["LLM", "モデル", "ローカル"])]},

 {"num":"🚀","name":"ローカルllmモデル速報","env":"LLM_MODEL","color":COL_MODEL,"sources":[
     rss("https://www.reddit.com/r/LocalLLaMA/hot/.rss?limit=30", include=LLM_MODEL_TERMS + ["Hugging Face", "release"]),
     gn('"Llama 4" OR "Qwen 3" OR "Qwen3" OR "Gemma 3" OR "Gemma3" OR "DeepSeek V3" OR "DeepSeek R1" OR "Phi-4" OR "Command R+" OR "Command R7" OR Nemotron',
        include=LLM_MODEL_TERMS, exclude=LLM_MODEL_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     gn('"Nous Hermes" OR "Nous Research" OR "Dolphin 3" OR "WizardLM" OR "Magnum-v" OR "MythoMax-L2" OR "Yi-34B" OR "Yi-6B" OR "Yi-1.5" OR "GLM-4" OR "GLM-5" OR InternLM OR "Falcon LLM" OR OLMo',
        include=LLM_MODEL_TERMS, exclude=LLM_MODEL_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     gn('"新モデル公開" OR "オープンウェイト" OR "モデル提供開始" OR "重み公開" OR "モデル公開" -トヨタ -ホンダ -日産 -スバル -マツダ -スズキ -ルーミー -GRパーツ -愛車 -カスタムカー -くるまのニュース -"Motor Fan" -福井新聞 -徳井義実 -レクサス -テニステンプル -"ラサロ・ガルシア" -ミンテギ -"バレンシア 2026" -テニス -"デル・オルモ"',
        include=LLM_MODEL_TERMS, exclude=LLM_MODEL_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     rss("https://news.google.com/rss/search?q=%22open%20weights%22%20OR%20%22weights%20released%22%20OR%20%22available%20on%20Hugging%20Face%22%20OR%20%22released%20on%20HF%22%20OR%20%22HuggingFace%20release%22&hl=en-US&gl=US&ceid=US:en",
         include=LLM_MODEL_TERMS, exclude=LLM_MODEL_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     gn('site:x.com "Llama 4" OR "Qwen 3" OR "Gemma 3" OR "DeepSeek V3" OR "DeepSeek R1" OR "Phi-4" OR "Command R"',
        include=LLM_MODEL_TERMS, exclude=LLM_MODEL_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     rss("https://zenn.dev/topics/llama/feed", include=LLM_MODEL_TERMS),
     rss("https://zenn.dev/topics/qwen/feed", include=LLM_MODEL_TERMS),
     rss("https://zenn.dev/topics/gemma/feed", include=LLM_MODEL_TERMS),
     rss("https://zenn.dev/topics/llm/feed", include=LLM_MODEL_TERMS + ["リリース", "モデル"]),
     rss("https://zenn.dev/topics/gpt/feed", include=LLM_MODEL_TERMS + ["ローカル", "オープン"]),
     rss("https://mistral.ai/rss.xml", include=LLM_MODEL_TERMS + ["Mistral"]),
     rss("https://huggingface.co/blog/feed.xml", include=LLM_MODEL_TERMS + ["release", "launch"]),
     rss("https://deepmind.google/blog/rss.xml", include=LLM_MODEL_TERMS),
     rss("https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", include=LLM_MODEL_TERMS),
     # ★2026-09-16 盛々: Qiita LLM(fresh4/pass2) + Zenn qwen(fresh6/pass5・強い)
     rss("https://qiita.com/tags/llm/feed", include=LLM_MODEL_TERMS + ["LLM", "モデル"]),
     rss("https://qiita.com/tags/%E6%A9%9F%E6%A2%B0%E5%AD%A6%E7%BF%92/feed", include=LLM_MODEL_TERMS + ["LLM", "モデル"]),
     # ★2026-09-18 v4: 激裏ACTIVE_CONFIRMED＋rss_discover実測
     rss("https://openai.com/news/rss.xml", include=LLM_MODEL_TERMS + ["release", "model", "GPT", "Sora"], exclude=LLM_MODEL_EXCLUDE),
     rss("https://blog.google/innovation-and-ai/technology/ai/rss/", include=LLM_MODEL_TERMS + ["Gemini", "model", "release"], exclude=LLM_MODEL_EXCLUDE),
     rss("https://github.com/openai/codex/releases.atom", include=LLM_MODEL_TERMS + ["codex", "release"]),
     rss("https://www.reddit.com/r/singularity/new/.rss?limit=25", include=LLM_MODEL_TERMS + ["release", "model", "launch"]),
     rss("https://arstechnica.com/ai/feed/", include=LLM_MODEL_TERMS + ["model", "release", "launch"]),
     rss("https://www.marktechpost.com/feed/", include=LLM_MODEL_TERMS + ["model", "release", "launch"]),
     rss("https://simonwillison.net/atom/everything/", include=LLM_MODEL_TERMS + ["model", "release"]),
     rss("https://aismiley.co.jp/ai_news/feed/", include=LLM_MODEL_TERMS + ["モデル", "リリース", "公開"])]},

 {"num":"⚡","name":"ローカル最適化・量子化速報","env":"QUANT","color":COL_QUANT,"sources":[
     rss("https://www.reddit.com/r/LocalLLaMA/search.rss?q=quantization+OR+GGUF+OR+AWQ+OR+EXL2&restrict_sr=on&sort=new&limit=25",
         include=QUANT_TERMS + ["LLM", "model"]),
     gn('"GGUF" OR "AWQ quant" OR "EXL2" OR "MXFP4" OR "bitsandbytes" OR "Marlin kernel" OR "TorchAO" OR "HQQ"',
        include=QUANT_TERMS, exclude=QUANT_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     gn('"量子化" OR "推論最適化" OR "vLLM" OR "TensorRT-LLM" OR "SGLang" OR "投機的デコード" OR "推論高速化"',
        include=QUANT_TERMS + ["LLM"], exclude=QUANT_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     # ★2026-09-18 盛々: llama.cpp/KV cache/FlashAttention/MoE系
     gn('"llama.cpp" OR "FlashAttention" OR "PagedAttention" OR "KV cache" OR "ExLlamaV2" OR "GPTQ-quant" OR "AWQ quant" OR "IQ4_XS" OR "Q4_K_M"',
        include=QUANT_TERMS, exclude=QUANT_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     rss("https://news.google.com/rss/search?q=%22vLLM%22%20OR%20%22SGLang%22%20OR%20%22TensorRT-LLM%22%20OR%20%22MLX%22%20OR%20%22quantization%22%20OR%20%22GGUF%22&hl=en-US&gl=US&ceid=US:en",
         include=QUANT_TERMS, exclude=QUANT_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     gn('site:x.com "GGUF" OR "quantization" OR "EXL2" OR "AWQ" OR "MXFP4" OR "vLLM" -"Astral Quantization" -Grimoire -Psytrance -HP9 -HP3 -"最短焦点距離" -"Ver. 6.0" -"HiTech Dark" -"Dj Grimoire" -Arcaea -"Hardcore Psytrance" -"7 RSR" -佗棄',
        include=QUANT_TERMS, exclude=QUANT_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     rss("https://zenn.dev/topics/gguf/feed", include=QUANT_TERMS + ["LLM"]),
     rss("https://zenn.dev/topics/llm/feed", include=QUANT_TERMS),
     rss("https://zenn.dev/topics/ai/feed", include=QUANT_TERMS),
     rss("https://zenn.dev/topics/machinelearning/feed", include=QUANT_TERMS),
     rss("https://huggingface.co/blog/feed.xml", include=QUANT_TERMS + ["inference"]),
     rss("https://github.com/vllm-project/vllm/releases.atom", include=QUANT_TERMS + ["performance"]),
     rss("https://www.together.ai/blog/rss.xml", include=QUANT_TERMS),
     # ★2026-09-16 盛々: Qiita llama.cpp(実測fresh2/pass1)
     rss("https://qiita.com/tags/llama.cpp/feed", include=QUANT_TERMS + ["llama.cpp", "GGUF"]),
     # ★2026-09-18 v4: rss_discover実測(24h fresh 3-25件) + include フィルタで量子化に絞る
     rss("https://www.reddit.com/r/singularity/new/.rss?limit=25", include=QUANT_TERMS + ["quantization", "GGUF", "vLLM"]),
     rss("https://www.marktechpost.com/feed/", include=QUANT_TERMS + ["quantization", "inference"]),
     rss("https://arstechnica.com/ai/feed/", include=QUANT_TERMS + ["quantization", "inference"]),
     rss("https://simonwillison.net/atom/everything/", include=QUANT_TERMS + ["GGUF", "llama.cpp", "MLX"])]},

 {"num":"👁️","name":"ローカルvlm・マルチモーダル速報","env":"VLM","color":COL_VLM,"sources":[
     rss("https://www.reddit.com/r/LocalLLaMA/search.rss?q=VLM+OR+vision+OR+multimodal&restrict_sr=on&sort=new&limit=25",
         include=VLM_TERMS),
     gn('"Qwen2.5-VL" OR "Qwen2-VL" OR "Qwen3-VL" OR "InternVL" OR Molmo OR "Llama 3.2 Vision" OR Pixtral OR "MiniCPM-V" OR LLaVA',
        include=VLM_TERMS, exclude=VLM_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     # ★2026-09-18 盛々: 2025-2026新モデル(SmolVLM/PaliGemma/Florence/Gemma3等)
     gn('"SmolVLM" OR "PaliGemma" OR "Florence-2" OR "Gemma 3" OR "DeepSeek-VL2" OR "GLM-4V" OR "GLM-4.5V" OR "InternVL 3" OR "Ovis 2" OR "MiniCPM-o"',
        include=VLM_TERMS, exclude=VLM_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     gn('"Aya Vision" OR "Pixtral-Large" OR "Phi-4-multimodal" OR "LLaVA-OneVision" OR "CogVLM2" OR "InternVL 2.5" OR "Idefics3" OR "Molmo-72B" OR "Mistral Small 3"',
        include=VLM_TERMS, exclude=VLM_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     gn('"マルチモーダル" OR "画像理解AI" OR "vision-language" OR "動画理解" -骨髄腫 -臨床 -"生存予測" -"予後予測" -腫瘍 -がん -医療研究 -東大 -理研 -日経Robotics',
        include=VLM_TERMS, exclude=VLM_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     rss("https://news.google.com/rss/search?q=%22vision%20language%20model%22%20OR%20%22VLM%22%20OR%20%22multimodal%20LLM%22%20OR%20%22Qwen-VL%22%20OR%20%22InternVL%22&hl=en-US&gl=US&ceid=US:en",
         include=VLM_TERMS, exclude=VLM_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     gn('site:x.com "Qwen-VL" OR "Qwen2-VL" OR "InternVL" OR "Molmo" OR "Pixtral" OR "MiniCPM-V" OR "LLaVA"',
        include=VLM_TERMS, exclude=VLM_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     rss("https://zenn.dev/topics/vlm/feed", include=VLM_TERMS),
     rss("https://zenn.dev/topics/ai/feed", include=VLM_TERMS + ["vision", "VLM", "マルチモーダル"]),
     rss("https://zenn.dev/topics/llm/feed", include=VLM_TERMS + ["vision", "マルチモーダル"]),
     rss("https://zenn.dev/topics/machinelearning/feed", include=VLM_TERMS),
     rss("https://zenn.dev/topics/deeplearning/feed", include=VLM_TERMS),
     rss("https://huggingface.co/blog/feed.xml", include=VLM_TERMS + ["vision", "multimodal"]),
     rss("https://export.arxiv.org/rss/cs.CV", include=VLM_TERMS),
     # ★2026-09-18 v4: rss_discover実測(24h fresh 3-25件) + VLM include フィルタ
     rss("https://www.reddit.com/r/singularity/new/.rss?limit=25", include=VLM_TERMS + ["multimodal", "vision"]),
     rss("https://www.marktechpost.com/feed/", include=VLM_TERMS + ["multimodal", "vision"]),
     rss("https://arstechnica.com/ai/feed/", include=VLM_TERMS + ["multimodal", "vision"]),
     rss("https://simonwillison.net/atom/everything/", include=VLM_TERMS + ["vision", "multimodal"])]},

 {"num":"🎤","name":"ローカルstt・音声認識速報","env":"STT","color":COL_STT,"sources":[
     rss("https://www.reddit.com/r/LocalLLaMA/search.rss?q=Whisper+OR+transcription+OR+speech&restrict_sr=on&sort=new&limit=20",
         include=STT_TERMS),
     gn('"OpenAI Whisper" OR "whisper.cpp" OR "faster-whisper" OR WhisperX OR "distil-whisper" OR "NeMo Parakeet" OR SenseVoice',
        include=STT_TERMS, exclude=STT_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     # ★2026-09-18 盛々: 2025-2026新モデル(Moonshine/Kyutai/Whisper Turbo/Kotoba Whisper等)
     gn('"Moonshine" OR "Kyutai STT" OR "Whisper Turbo" OR "Whisper Large v3" OR "Kotoba Whisper" OR "Parakeet TDT" OR "ReazonSpeech"',
        include=STT_TERMS, exclude=STT_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     gn('"音声認識" OR "文字起こしAI" OR "音声のテキスト化" OR "Whisper" -RECAIUS -東芝 -カスハラ -コールセンター -"対策義務化" -"オンデマンド配信" -イザ！ -Salomon -"XT-Whisper" -Habibi -Carpet -スニダン -"KEYS IN PURPLE" -激ロック -"a whisper" -映画館 -"時のオカリナ" -"Switch 2" -Gamer -AmiVoice -QuickSummary -BIZTEL -AIsmiley -"迷える流れ星" -マリオネットドールズ -"東京マリオネット" -"音声認識マイクモジュール" -"市場規模" -CAGR -岐阜名鉄タクシー -"配車システム" -"字幕表示モニター" -大山崎町 -"多言語案内看板" -西条 -"京都府大山崎町議会" -"聴覚障害者" -BOXIL -"アプリ標準搭載" -エプソン -Endeavor',
        include=STT_TERMS, exclude=STT_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     rss("https://news.google.com/rss/search?q=%22OpenAI%20Whisper%22%20OR%20%22whisper.cpp%22%20OR%20%22faster-whisper%22%20OR%20%22NeMo%20Parakeet%22%20OR%20%22SenseVoice%22&hl=en-US&gl=US&ceid=US:en",
         include=STT_TERMS, exclude=STT_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     gn('site:x.com "whisper.cpp" OR "faster-whisper" OR "WhisperX" OR "NeMo Parakeet" OR "SenseVoice"',
        include=STT_TERMS, exclude=STT_EXCLUDE, title_exclude=LOCAL_TITLE_EXCLUDE),
     rss("https://zenn.dev/topics/whisper/feed", include=STT_TERMS),
     rss("https://zenn.dev/topics/ai/feed", include=STT_TERMS + ["音声", "STT", "文字起こし"]),
     rss("https://huggingface.co/blog/feed.xml", include=STT_TERMS + ["audio", "speech"]),
     rss("https://export.arxiv.org/rss/eess.AS", include=STT_TERMS),
     rss("https://github.com/openai/whisper/releases.atom", include=STT_TERMS + ["update", "model"]),
     # ★2026-09-16 盛々: 日本語ソース追加(Qiita/Zenn音声認識・実測 fresh 2-4件・低頻度だが良質)
     rss("https://qiita.com/tags/%E9%9F%B3%E5%A3%B0%E8%AA%8D%E8%AD%98/feed", include=STT_TERMS + ["Whisper", "音声"]),
     rss("https://qiita.com/tags/whisper/feed", include=STT_TERMS + ["Whisper", "文字起こし"]),
     rss("https://zenn.dev/topics/%E9%9F%B3%E5%A3%B0%E8%AA%8D%E8%AD%98/feed", include=STT_TERMS + ["Whisper", "音声"]),
     # ★2026-09-18 v4: rss_discover実測 + STT include フィルタ
     rss("https://www.reddit.com/r/singularity/new/.rss?limit=25", include=STT_TERMS + ["Whisper", "speech recognition"]),
     rss("https://www.marktechpost.com/feed/", include=STT_TERMS + ["Whisper", "speech recognition", "ASR"]),
     rss("https://simonwillison.net/atom/everything/", include=STT_TERMS + ["Whisper", "transcription"])]},
]

def gn_url(q):
    return "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=ja&gl=JP&ceid=JP:ja"

def entry_epoch(e):
    # 記事の公開時刻(UTC epoch)。無ければNone。速報化(時刻ソート・時間窓)の心臓部。
    for k in ("published_parsed", "updated_parsed"):
        tm = e.get(k)
        if tm:
            try:
                return calendar.timegm(tm)
            except Exception:
                pass
    return None

def clean(t):
    t = re.sub(r"<[^>]+>", " ", t or "")
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", t).strip()

def contains_any(text, terms):
    if not terms: return True
    low = text.lower()
    for term in terms:
        needle = str(term).lower()
        if not needle:
            continue
        if len(needle) <= 2 and needle.isascii() and needle.isalnum():
            if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", low):
                return True
            continue
        if needle in low:
            return True
    return False

def is_release_version_noise(title):
    t = title.strip()
    low = t.lower()
    if any(x.lower() in low for x in MODEL_RELEASE_TERMS + ["codex", "claude", "gemini", "grok", "llama", "qwen", "deepseek"]):
        return False
    return bool(re.fullmatch(r"v?\d+(\.\d+){1,4}([._-]?(alpha|beta|rc)\.?\d*)?", low) or low in {"stable", "nightly"})

def canonical_title(title):
    t = re.sub(r"\s+-\s+[^|]+(?:\s+\|.*)?$", "", title or "")
    t = re.sub(r"\s*\([^)]{2,50}\)\s*$", "", t)
    t = re.sub(r"\s*（[^）]{2,50}）\s*$", "", t)
    return re.sub(r"\s+", " ", t).strip().lower()

def title_from_url(url):
    path = urllib.parse.urlparse(url).path.strip("/")
    slug = path.split("/")[-1] if path else urllib.parse.urlparse(url).netloc
    slug = urllib.parse.unquote(slug)
    return re.sub(r"[-_]+", " ", slug).strip().title()

def fetch_sitemap(src):
    try:
        req = urllib.request.Request(src["url"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml = resp.read(500000).decode("utf-8", "replace")
    except Exception as e:
        print("    sitemap失敗:", src["url"][:50], e); return []
    include = src.get("include") or []
    exclude = GLOBAL_EXCLUDE + (src.get("exclude") or [])
    title_include = src.get("title_include") or []
    title_exclude = src.get("title_exclude") or []
    seen_titles = set()
    prefixes = src.get("path_prefix") or []
    entries = []
    for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
        loc_m = re.search(r"<loc>(.*?)</loc>", block, re.S)
        if not loc_m: continue
        loc = clean(loc_m.group(1))
        parsed = urllib.parse.urlparse(loc)
        path = parsed.path or "/"
        if prefixes and not any(path.startswith(prefix) for prefix in prefixes): continue
        title = title_from_url(loc)
        lastmod_m = re.search(r"<lastmod>(.*?)</lastmod>", block, re.S)
        lastmod = clean(lastmod_m.group(1)) if lastmod_m else ""
        filter_text = " ".join([title, path])
        if title_include and not contains_any(title, title_include): continue
        if title_exclude and contains_any(title, title_exclude): continue
        if include and not contains_any(filter_text, include): continue
        if exclude and contains_any(filter_text, exclude): continue
        summ = f"official page updated: {lastmod[:10]}" if lastmod else ""
        entries.append((lastmod, title, loc, summ, src.get("label") or parsed.netloc))
    entries.sort(key=lambda x: x[0], reverse=True)
    return [(title, loc, summ, label) for lastmod, title, loc, summ, label in entries[:PER_SOURCE]]

# ---- 日本語化（英語タイトル/要約をGoogle翻訳で和訳・失敗時は原文＝重要ニュースは英語でも可）----
# ★deep_translator(内部でrequests使用)はライブラリ側にtimeoutを渡す口が無く、
#   GitHub Actionsのクラウド側IPからだと接続がハングして戻ってこないことがある
#   （ローカルでは問題なくAction上でだけ無限待ちした実例2026-07-08）。
#   スレッド+timeoutで包んでも、ワーカースレッド自体が本当にハングした場合は
#   Pythonプロセス終了時のスレッドjoin待ちで結局終わらない恐れがある。
#   なので外部ライブラリを経由せず urllib.request.urlopen(timeout=...) で
#   Google翻訳の非公式エンドポイントを直接叩く＝ソケットレベルの本物のタイムアウトにする。
_JP_RE = re.compile(r"[ぁ-んァ-ヶ一-龠]")
_TR_TIMEOUT_SEC = 8
_TR_MAX_FAIL = 3          # 1エンジンがこの回数連続で失敗したら、そのエンジンだけ以後スキップ(他は生かす)

def is_ja(t):
    if not t: return True
    return len(_JP_RE.findall(t)) >= max(3, int(len(t) * 0.12))   # 既に日本語なら翻訳しない

# ---- 翻訳（★GIT内で完結。外部の翻訳APIサービスを実行時に一切叩かない）----
# 2026-09-03 Hikârư制約: 「GIT+Discord完結」。外部翻訳API(Google非公式/MyMemory/Gemini等)は使わない。
#   旧実装は Google翻訳の非公式endpointを叩いていたが、GitHub ActionsのIPから弾かれ
#   (ログ実物「翻訳サーバー応答なし(6s×3回連続) → 以後は原文のまま投稿」)、
#   実機で英語のまま98件・論文は21件中14件が未翻訳という状態を作っていた。
#   → argostranslate(CTranslate2ベース)でrunner内ローカル推論に変更。
#     実測: モデル準備10.9s、翻訳は1件目7.1s(ロード込)、2件目以降 0.06〜0.10s。
_ARGOS = None   # None=未初期化 / True=使える / False=使えない

def _argos_ready():
    global _ARGOS
    if _ARGOS is not None:
        return _ARGOS
    try:
        import argostranslate.package as P
        import argostranslate.translate as T
        codes = {l.code for l in T.get_installed_languages()}
        if not ("en" in codes and "ja" in codes):
            P.update_package_index()
            pkgs = [p for p in P.get_available_packages()
                    if p.from_code == "en" and p.to_code == "ja"]
            if not pkgs:
                print("    argostranslate: en->ja パッケージが見つからない")
                _ARGOS = False
                return _ARGOS
            P.install_from_path(pkgs[0].download())
        _ARGOS = True
    except Exception as e:
        print(f"    argostranslate 準備失敗: {type(e).__name__}: {str(e)[:80]}")
        _ARGOS = False
    return _ARGOS

def to_ja(t):
    """英文を日本語へ。runner内のローカル推論のみを使い、外部APIは叩かない。
       失敗時は原文のまま返す(重要ニュースは英語でも出す)。"""
    if not t or is_ja(t):
        return t
    if not _argos_ready():
        return t
    try:
        import argostranslate.translate as T
        out = T.translate(t[:1500], "en", "ja")
        return out if (out and is_ja(out)) else t
    except Exception:
        return t

def load_webhooks():
    m = {}
    for k, v in os.environ.items():
        if k.startswith("DISCORD_LOCALAI_WEBHOOK_"):
            m[k.replace("DISCORD_LOCALAI_WEBHOOK_", "")] = v
    if not m and os.path.exists(WEBHOOKS_JSON):
        for r in json.load(io.open(WEBHOOKS_JSON, encoding="utf-8")):
            if r.get("webhook_url"):
                m[r["env"].replace("DISCORD_LOCALAI_WEBHOOK_", "")] = r["webhook_url"]
    return m

def load_seen():
    try: return set(json.load(io.open(SEEN_FILE, encoding="utf-8")))
    except Exception: return set()

def save_seen(s):
    io.open(SEEN_FILE, "w", encoding="utf-8").write(json.dumps(sorted(s), ensure_ascii=False))

def fetch(src):
    if src["type"] == "sitemap":
        return fetch_sitemap(src)
    url = gn_url(src["q"]) if src["type"] == "gn" else src["url"]
    try:
        f = feedparser.parse(url)
    except Exception as e:
        print("    fetch失敗:", url[:50], e); return []
    feed_label = clean((f.feed.get("title", "") if getattr(f, "feed", None) else "")) or urllib.parse.urlparse(url).netloc
    include = src.get("include") or src.get("must") or []
    exclude = GLOBAL_EXCLUDE + (src.get("exclude") or [])
    title_include = src.get("title_include") or []
    title_exclude = src.get("title_exclude") or []
    # ★速報化: 全エントリを公開時刻で新しい順にソート(先頭 f.entries[:PER_SOURCE*5] 固定を廃止)。
    #          時刻が取れないものは後ろに回す(フィード順)。
    dated = [(entry_epoch(e), e) for e in (getattr(f, "entries", []) or [])]
    dated.sort(key=lambda x: (x[0] is not None, x[0] or 0.0), reverse=True)
    out = []
    seen_titles = set()
    for ts, e in dated:
        # ★時間窓: 公開時刻が分かるものは直近 FRESH_HOURS 時間だけを速報として通す。古いものは捨てる。
        if ts is not None and (NOW - ts) > FRESH_HOURS * 3600:
            continue
        title = clean(e.get("title", ""))
        if not title: continue
        summ = clean(e.get("summary", "") or e.get("description", ""))
        entry_source = e.get("source", {})
        if isinstance(entry_source, dict):
            entry_source = clean(entry_source.get("title", ""))
        else:
            entry_source = ""
        filter_text = " ".join([title, summ, entry_source])
        if is_release_version_noise(title): continue
        title_key = canonical_title(title)
        if title_key in seen_titles: continue
        if title_include and not contains_any(title, title_include): continue
        if title_exclude and contains_any(title, title_exclude): continue
        if include and not contains_any(filter_text, include): continue
        if exclude and contains_any(filter_text, exclude): continue
        seen_titles.add(title_key)
        if len(summ) < 25 or summ[:18] == title[:18]: summ = ""
        summ = summ[:160] + ("…" if len(summ) > 160 else "")
        out.append((title, e.get("link", ""), summ, src.get("label") or entry_source or feed_label))
        if len(out) >= PER_SOURCE: break
    return out

def collect_topic_items(t, seen, sleep_sec=0.4):
    # ★2026-09-08 修正: タイトルのキーは【部屋ごと】に持つ。
    #   2026-09-05にタイトル併用を入れた時、seenが全トピック共通の1つの集合なので
    #   「同じ記事は15部屋のどこか1つにしか出せない」状態になっていた。
    #   実測(9/8): タイトルで止まっていた7件は【7件とも別の部屋にだけ出ていた】
    #     例: 「M365 CopilotでもGPT-6 Astra利用可能に」→チャッピーに出て★copilotに出せない
    #         「Gemini 3.8 Flash提供開始」→geminiに出て★新モデルリリースに出せない
    #   15分類は「分類が重なる記事は両方に出る」のが仕様なので、部屋名を接頭辞に付ける。
    #   ★URLの方は従来どおり全部屋共通のまま（同じURLは1回だけ、は元からの設計）。
    room = t["env"] + "|"
    source_hits = []
    keys = set()
    for src in t["sources"]:
        rows = []
        for title, link, summ, srclabel in fetch(src):
            title_key = canonical_title(title)
            # ★2026-09-05 実測: Google Newsは同じ記事に【URLを3種類】振ってくる。
            #   seenがlinkしか持っていなかったため、同じ記事が媒体違いで何度も流れていた。
            #   実測(直近100件×15CH): URLだけ=121件しか止まらない / タイトルも見る=256件(+135)。
            #   誤爆の確認: 同じキーで原文が違った81組は【全部が媒体名の違いだけ】=同じ記事。
            #              12字未満の短いキーで重複扱いになったのは1組のみ(それも原文1種類)。
            if (not link or link in seen or (room + title_key) in seen
                    or link in keys or title_key in keys):
                continue
            keys.add(link)
            keys.add(title_key)
            rows.append((title, link, summ, srclabel))
        if rows:
            source_hits.append(rows)
        if sleep_sec:
            time.sleep(sleep_sec)

    picked = []
    picked_keys = set()
    def add_item(item):
        key = item[1] or canonical_title(item[0])
        if key in picked_keys:
            return
        picked_keys.add(key)
        picked.append(item)

    # Google Newsだけで枠を埋めない。朝活方式として、実証済み母体を分類別に混ぜる。
    for rows in source_hits:
        if len(picked) >= PER_CHANNEL:
            break
        add_item(rows[0])

    if len(picked) < PER_CHANNEL:
        for rows in source_hits:
            for item in rows[1:]:
                if len(picked) >= PER_CHANNEL:
                    break
                add_item(item)
            if len(picked) >= PER_CHANNEL:
                break
    return picked[:PER_CHANNEL]

def post(url, header, items, color):
    embeds = []
    for title, link, summ, src in items:
        emb = {"title": title[:250], "url": link, "color": color, "footer": {"text": src[:100]}}
        if summ: emb["description"] = summ
        embeds.append(emb)
    body = {"content": header, "embeds": embeds[:10]}
    r = urllib.request.Request(url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
    # ★2026-09-03: 旧実装は while True で429を無限リトライしており、Discord側のレート制限が
    #   続くと run が終わらなくなる(実測: 1runが16分以上 in_progress のまま残り、concurrencyで
    #   後続の【自動起動run】が cancelled になった＝自動配信が殺された)。
    #   リトライ回数・1回の待ち・累計待ちの3つに上限を入れて必ず抜ける。
    MAX_RETRY = 5
    total_wait = 0.0
    for attempt in range(MAX_RETRY):
        try:
            with urllib.request.urlopen(r, timeout=20) as x: return x.status
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try: retry = float(json.loads(e.read()).get("retry_after", 1.0))
                except Exception: retry = 1.0
                retry = min(retry, 10.0)                      # 1回の待ちの上限
                total_wait += retry + 0.3
                if attempt >= MAX_RETRY - 1 or total_wait > 30:
                    return f"429:give_up(try{attempt+1},wait{total_wait:.0f}s)"
                time.sleep(retry + 0.3); continue
            return f"{e.code}:{e.read().decode('utf-8','replace')[:120]}"
        except Exception as e:
            return f"ERR:{type(e).__name__}"
    return "429:give_up"

def main():
    hooks = load_webhooks(); seen = load_seen()
    _ORDER = ['QUANT', 'VLM', 'STT', 'LLM_MODEL', 'LLM_FW']
    globals()["TOPICS"] = sorted(TOPICS, key=lambda t: _ORDER.index(t["env"]) if t["env"] in _ORDER else 99)
    print(f"webhooks={len(hooks)} seen={len(seen)} topics={len(TOPICS)}")
    for t in TOPICS:
        url = hooks.get(t["env"])
        if not url:
            print(f"{t['num']} {t['name']} … webhook未設定スキップ"); continue
        picked = collect_topic_items(t, seen)
        if not picked:
            print(f"{t['num']} {t['name']} … 新規なし"); continue
        # ★翻訳の【前】のキーを先に取る。to_ja後に取ると日本語で保存して英語で照合するズレが出る。
        keys_before = [canonical_title(ti) for (ti, _, _, _) in picked]
        picked = [(to_ja(ti), li, to_ja(su), sr) for (ti, li, su, sr) in picked]  # 英語→日本語（失敗時は原文）
        st = post(url, f"**{t['num']}｜{t['name']}**", picked, t["color"])
        room = t["env"] + "|"             # ★部屋ごとに持つ（分類をまたぐ掲載は殺さない）
        for (ti, link, _, _), k0 in zip(picked, keys_before):
            seen.add(link)
            seen.add(room + k0)               # ★翻訳前（同じ英語記事が別フィードから来た時に効く）
            seen.add(room + canonical_title(ti))  # ★翻訳後（Discordに出ている形と一致させる）
        print(f"{t['num']} {t['name']} … {len(picked)}件 ({st})")
        time.sleep(1.3)
    save_seen(seen); print("seen保存:", len(seen))

if __name__ == "__main__":
    main()
