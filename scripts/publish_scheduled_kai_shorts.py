import json
import sys
import time
from pathlib import Path

ROOT = Path("AutoClip").resolve()
sys.path.insert(0, str(ROOT))

from autoclip_app.youtube import upload_public_video, youtube_status

SUMMARY_FILE = ROOT / "data" / "outputs" / "kai_shorts" / "kai_summary.json"

CLIPS_META = {
    "short_1_rendered.mp4": {
        "title": "KAI CENAT: WE ARE NOT READY FOR GTA 6! 😱🎮 #Shorts",
        "description": "Kai Cenat says we are definitely not ready for GTA 6 after looking at GTA 5 graphics! Do you agree? 🎮💀\n\nDrop a like and subscribe for daily streamer clips!\n\n#KaiCenat #GTA6 #GTA5 #Gaming #Shorts #TheKM #Viral #Trending",
        "tags": ["Shorts", "Kai Cenat", "GTA 6", "GTA 5", "Gaming", "Viral", "TheKM", "Rockstar Games", "Trending"],
        "category_id": "20"
    },
    "short_2_rendered.mp4": {
        "title": "TREVOR'S MOST UNHINGED OUT-OF-POCKET MOMENT?! 💀😭 #Shorts",
        "description": "Kai Cenat's reaction to Trevor Phillips saying he needs to meditate or masturbate in GTA 5 is priceless! 💀😭\n\nSubscribe for the funniest GTA moments!\n\n#Shorts #KaiCenat #GTA5 #TrevorPhilips #Funny #Gaming #Meme #Trending",
        "tags": ["Shorts", "Kai Cenat", "GTA 5", "Trevor Phillips", "Funny Moments", "Gaming", "Viral", "Meme", "Trending"],
        "category_id": "20"
    },
    "short_3_rendered.mp4": {
        "title": "KAI CENAT GIVES AWAY 3 PS5s ON GTA 5 STREAM?! 🎮🔥 #Shorts",
        "description": "Kai Cenat announcing a 3x PS5 giveaway live during his GTA 5 marathon stream! 🎮🔥\n\nSubscribe for more legendary stream moments!\n\n#Shorts #KaiCenat #GTA5 #PS5 #Giveaway #Gaming #TheKM #Viral #Trending",
        "tags": ["Shorts", "Kai Cenat", "GTA 5", "PS5", "Giveaway", "Gaming", "TheKM", "Viral", "Trending"],
        "category_id": "20"
    }
}

def main():
    print("=== AutoClip Scheduled Uploader ===")
    stat = youtube_status()
    print("YouTube Status:", stat.get("message"))
    if not stat.get("connected"):
        print("ERROR: YouTube not connected.")
        return

    out_dir = ROOT / "data" / "outputs" / "kai_shorts"
    results = []

    for filename, meta in CLIPS_META.items():
        video_path = out_dir / filename
        if not video_path.exists():
            print(f"File not found: {video_path}")
            continue

        print(f"\nPublishing: {meta['title']}...")
        try:
            res = upload_public_video(
                video_path=video_path,
                title=meta["title"],
                description=meta["description"],
                tags=meta["tags"],
                category_id=meta["category_id"]
            )
            vid = res.get("video_id")
            y_url = f"https://youtube.com/shorts/{vid}" if vid else "Uploaded"
            print(f"✅ PUBLISHED: {y_url}")
            results.append({"title": meta["title"], "url": y_url, "id": vid, "status": "LIVE"})
        except Exception as exc:
            err = str(exc)
            print(f"⚠️ Notice: {err}")
            results.append({"title": meta["title"], "file": str(video_path), "status": f"Pending: {err}"})

        time.sleep(2)

    print("\nSummary:", json.dumps(results, indent=2))

if __name__ == "__main__":
    main()
