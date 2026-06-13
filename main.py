import os
import yaml
import feedparser
from datetime import datetime
from ebooklib import epub

OUTPUT_DIR = "articles"


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_dir():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)


def build_epub(articles, title):
    book = epub.EpubBook()
    book.set_title(title)
    book.set_language("zh")

    chapters = []

    for i, a in enumerate(articles):
        c = epub.EpubHtml(
            title=a["title"],
            file_name=f"chap_{i}.xhtml"
        )

        c.content = f"""
        <h1>{a['title']}</h1>
        <p>{a['content']}</p>
        <a href="{a['link']}">原文</a>
        """

        book.add_item(c)
        chapters.append(c)

    book.toc = chapters
    book.spine = ["nav"] + chapters

    return book


def fetch_articles(feeds):
    results = []

    for feed in feeds:
        if not feed.get("enabled", True):
            continue

        url = feed["url"]
        title = feed.get("title", "RSS")

        parsed = feedparser.parse(url)

        for entry in parsed.entries[:20]:
            results.append({
                "title": entry.get("title", ""),
                "content": entry.get("summary", ""),
                "link": entry.get("link", ""),
                "source": title
            })

    return results


def main():
    config = load_config()
    ensure_dir()

    feeds = config.get("Feeds", [])

    articles = fetch_articles(feeds)

    if not articles:
        print("❌ 没有获取到文章")
        return

    title = config.get("Settings", {}).get("filename_template", "RSS")

    date_str = datetime.now().strftime("%Y-%m-%d")

    filename = title.replace("{date}", date_str) + ".epub"

    book = build_epub(articles, title)

    output_path = os.path.join(OUTPUT_DIR, filename)

    epub.write_epub(output_path, book)

    print(f"✅ EPUB已生成: {output_path}")


if __name__ == "__main__":
    main()
