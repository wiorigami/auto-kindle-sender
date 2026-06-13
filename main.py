import feedparser
import yaml
import os
import json
from datetime import datetime, timedelta
from ebooklib import epub
import re
import base64
import requests
from urllib.parse import urlparse
import urllib3
from bs4 import BeautifulSoup
from readability import Document
from PIL import Image
import io

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def load_config():
    """读取配置（优先从环境变量，其次从文件）"""
    
    # 优先从环境变量读取完整配置（支持GitHub Variables）
    env_config = os.environ.get('CONFIG_YAML') or os.environ.get('RSS_CONFIG')
    if env_config:
        try:
            # 尝试作为YAML解析
            config = yaml.safe_load(env_config)
            print("✅ 使用环境变量配置（YAML格式）")
            return config
        except yaml.YAMLError:
            try:
                # 尝试作为JSON解析
                config = json.loads(env_config)
                print("✅ 使用环境变量配置（JSON格式）")
                return config
            except json.JSONDecodeError:
                print("⚠️ 环境变量配置格式错误，尝试使用文件配置")
    
    # 从文件读取配置
    config_file = os.environ.get('CONFIG_FILE', 'config.yaml')
    if os.path.exists(config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        print(f"✅ 使用配置文件: {config_file}")
        return config
    
    # 使用示例配置
    if os.path.exists('config.example.yaml'):
        with open('config.example.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        print("⚠️ 使用示例配置文件")
        return config
    
    raise FileNotFoundError("未找到配置文件或环境变量")

def fetch_feed(url):
    """拉取 RSS feed"""
    return feedparser.parse(url)

def filter_entries(entries, max_history):
    """按日期过滤 RSS 条目"""
    if max_history == -1:
        return entries
    cutoff_date = datetime.now() - timedelta(days=max_history)
    filtered = []
    for entry in entries:
        if 'published_parsed' in entry:
            entry_date = datetime(*entry.published_parsed[:6])
            if entry_date >= cutoff_date:
                filtered.append(entry)
    return filtered

def sanitize_filename(name):
    """清理文件名非法字符"""
    return "".join(c if c.isalnum() else "_" for c in name)

def resolve_link_content(url, config=None):
    """从原始链接解析内容
    
    Args:
        url: 要解析的URL
        config: 解析配置，包含选择器等信息
    
    Returns:
        解析后的HTML内容，失败返回None
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        
        response = requests.get(url, headers=headers, timeout=15, verify=False)
        if response.status_code != 200:
            return None
            
        html_content = response.text
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 如果配置中有选择器，优先使用选择器
        if config and isinstance(config, dict):
            # 方案3: CSS选择器提取
            if 'selectors' in config:
                selectors = config['selectors']
                
                # 移除不需要的元素
                if 'remove' in selectors:
                    remove_selectors = selectors['remove']
                    if isinstance(remove_selectors, str):
                        remove_selectors = [s.strip() for s in remove_selectors.split(',')]
                    
                    for selector in remove_selectors:
                        for elem in soup.select(selector):
                            elem.decompose()
                
                # 提取内容
                if 'content' in selectors:
                    content_selectors = selectors['content']
                    if isinstance(content_selectors, str):
                        content_selectors = [s.strip() for s in content_selectors.split(',')]
                    
                    extracted_content = []
                    for selector in content_selectors:
                        elements = soup.select(selector)
                        if elements:
                            for elem in elements:
                                extracted_content.append(str(elem))
                            break  # 找到第一个匹配的选择器就停止
                    
                    if extracted_content:
                        return '\n'.join(extracted_content)
            
            # 如果配置指定使用readability或选择器失败，使用fallback
            if config.get('method') == 'readability' or config.get('fallback') == 'readability':
                # 方案2: 使用readability自动提取
                doc = Document(html_content)
                return doc.summary()
        
        # 默认使用readability
        doc = Document(html_content)
        return doc.summary()
        
    except Exception as e:
        print(f"解析链接失败 {url}: {e}")
        return None

def extract_images_from_html(html_content):
    """从 HTML 内容中提取图片 URL"""
    img_pattern = r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>'
    return re.findall(img_pattern, html_content, re.IGNORECASE)

def download_image_as_base64(url, timeout=10):
    """下载图片并转换为 base64，WebP格式自动转换为JPEG"""
    try:
        # 更完整的请求头，模拟真实浏览器
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'Sec-Fetch-Dest': 'image',
            'Sec-Fetch-Mode': 'no-cors',
            'Sec-Fetch-Site': 'cross-site',
        }
        
        # 添加 Referer 头（从 URL 推断）
        parsed = urlparse(url)
        if parsed.netloc:
            headers['Referer'] = f'{parsed.scheme}://{parsed.netloc}/'
        
        response = requests.get(url, timeout=timeout, headers=headers, verify=False)
        if response.status_code == 200:
            # 获取图片类型
            content_type = response.headers.get('content-type', 'image/jpeg')
            if 'image' in content_type or len(response.content) > 100:  # 确保有内容
                # 检查是否为WebP格式
                is_webp = 'webp' in content_type.lower() or url.lower().endswith('.webp')
                
                if is_webp:
                    try:
                        # 将WebP转换为JPEG
                        img = Image.open(io.BytesIO(response.content))
                        # 如果是RGBA模式，转换为RGB
                        if img.mode == 'RGBA':
                            # 创建白色背景
                            background = Image.new('RGB', img.size, (255, 255, 255))
                            background.paste(img, mask=img.split()[3])  # 使用alpha通道作为mask
                            img = background
                        elif img.mode != 'RGB':
                            img = img.convert('RGB')
                        
                        # 转换为JPEG
                        output = io.BytesIO()
                        img.save(output, format='JPEG', quality=85)
                        img_data = output.getvalue()
                        img_base64 = base64.b64encode(img_data).decode('utf-8')
                        content_type = 'image/jpeg'
                    except Exception:
                        # 如果转换失败，使用原始数据
                        img_base64 = base64.b64encode(response.content).decode('utf-8')
                else:
                    # 非WebP格式，直接使用
                    img_base64 = base64.b64encode(response.content).decode('utf-8')
                
                # 如果没有明确的 content-type，尝试从 URL 推断
                if 'image' not in content_type:
                    if '.png' in url.lower():
                        content_type = 'image/png'
                    elif '.gif' in url.lower():
                        content_type = 'image/gif'
                    else:
                        content_type = 'image/jpeg'
                return f"data:{content_type};base64,{img_base64}"
    except Exception as e:
        # 静默处理错误，避免过多输出
        pass
    return None

def process_content_images(content, load_images=True):
    """处理内容中的图片，将其转换为 base64 嵌入"""
    if not load_images:
        # 如果不加载图片，移除所有 img 标签
        return re.sub(r'<img[^>]*>', '', content)
    
    # 提取所有图片 URL
    img_urls = extract_images_from_html(content)
    
    # 存储成功下载的图片
    embedded_images = []
    
    # 替换图片 URL 为 base64
    for img_url in img_urls:
        base64_img = download_image_as_base64(img_url)
        if base64_img:
            # 创建新的 img 标签，确保格式正确
            new_img_tag = f'<img src="{base64_img}" alt="图片"/>'
            # 替换原始的 img 标签
            img_pattern = f'<img[^>]*src=["\']?{re.escape(img_url)}["\']?[^>]*>'
            content = re.sub(img_pattern, new_img_tag, content)
            embedded_images.append(img_url[:50])
        else:
            # 如果无法下载，保留原始 URL
            pass
    
    if embedded_images:
        print(f"  ✓ 成功嵌入 {len(embedded_images)} 张图片")
    
    return content

def download_and_add_image(book, url, img_id):
    """下载图片并添加到 EPUB 书籍中，WebP格式自动转换为JPEG"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': urlparse(url).scheme + '://' + urlparse(url).netloc + '/'
        }
        
        response = requests.get(url, timeout=10, headers=headers, verify=False)
        if response.status_code == 200 and len(response.content) > 100:
            # 确定图片类型
            content_type = response.headers.get('content-type', '')
            img_content = response.content
            
            # 检查是否为WebP格式
            is_webp = 'webp' in content_type.lower() or url.lower().endswith('.webp')
            
            if is_webp:
                try:
                    # 将WebP转换为JPEG
                    img = Image.open(io.BytesIO(response.content))
                    # 如果是RGBA模式，转换为RGB
                    if img.mode == 'RGBA':
                        # 创建白色背景
                        background = Image.new('RGB', img.size, (255, 255, 255))
                        background.paste(img, mask=img.split()[3])  # 使用alpha通道作为mask
                        img = background
                    elif img.mode != 'RGB':
                        img = img.convert('RGB')
                    
                    # 转换为JPEG
                    output = io.BytesIO()
                    img.save(output, format='JPEG', quality=85)
                    img_content = output.getvalue()
                    ext = 'jpg'
                    media_type = 'image/jpeg'
                except Exception:
                    # 如果转换失败，仍然使用原始WebP
                    ext = 'webp'
                    media_type = 'image/webp'
            elif 'png' in content_type or '.png' in url.lower():
                ext = 'png'
                media_type = 'image/png'
            elif 'gif' in content_type or '.gif' in url.lower():
                ext = 'gif'
                media_type = 'image/gif'
            else:
                ext = 'jpg'
                media_type = 'image/jpeg'
            
            # 创建 EPUB 图片项
            img_name = f'img_{img_id}.{ext}'
            img_item = epub.EpubImage()
            img_item.uid = f'image_{img_id}'
            img_item.file_name = f'images/{img_name}'
            img_item.media_type = media_type
            img_item.content = img_content
            
            book.add_item(img_item)
            return f'images/{img_name}'
    except:
        pass
    return None

def convert_to_epub(feeds, load_images=True, feeds_config=None, custom_filename=None):
    """将 RSS feed 转换为精美的 EPUB 电子书"""
    book = epub.EpubBook()
    
    # 设置书籍元数据
    current_date = datetime.now()
    book.set_identifier(f'rss-compilation-{current_date.strftime("%Y%m%d%H%M%S")}')
    book.set_title('RSS 推送')
    book.set_language('zh')
    book.add_author('KindleRSS')
    book.add_metadata('DC', 'description', '精心整理的 RSS 订阅内容合集')
    book.add_metadata('DC', 'date', current_date.strftime('%Y-%m-%d'))
    
    # 创建自定义主目录页 (Primary TOC)
    main_toc_page = epub.EpubHtml(title='目录', file_name='main_toc.xhtml', lang='zh')
    main_toc_content = f'''
    <html xmlns="http://www.w3.org/1999/xhtml">
    <head>
        <title>目录</title>
        <style>
            a {{ color: black; text-decoration: underline; }}
            ul {{ margin: 30px auto; max-width: 600px; }}
            li {{ margin: 15px 0; }}
            img {{ 
                page-break-inside: avoid;
                break-inside: avoid;
                display: block;
                max-width: 100%;
                height: auto;
            }}
            figure {{
                page-break-inside: avoid;
                break-inside: avoid;
            }}
        </style>
    </head>
    <body>
        <center>
            <h1>RSS 推送</h1>
            <p>{datetime.now().strftime('%Y-%m-%d')}</p>
        </center>
        <br/>
        <ul>
    '''
    
    book.spine = ['nav', main_toc_page]  # nav first, then custom TOC
    book.toc = []
    all_articles = []  # 存储所有文章用于导航
    img_counter = 0  # 图片计数器
    feed_index_pages = []  # 存储所有 feed 索引页信息
    
    feed_list = list(feeds.items())
    for feed_idx, (feed_key, feed_data) in enumerate(feed_list):
        # 处理新旧数据格式兼容性
        if isinstance(feed_data, dict) and 'entries' in feed_data:
            entries = feed_data['entries']
            feed_meta = feed_data.get('feed_meta', {})
            config_name = feed_data.get('config_name')
        else:
            # 兼容旧格式（直接是 entries 列表）
            entries = feed_data
            feed_meta = {}
            config_name = None
            
        if not entries:
            continue
            
        # 优先使用 config name, 其次 feed title, 最后用 feed_key
        feed_name = config_name or feed_meta.get('title', feed_key)
        
        # 创建 feed 索引页（Secondary TOC）
        index_file = sanitize_filename(feed_name) + "_toc.xhtml"
        feed_index_page = epub.EpubHtml(title=feed_name, file_name=index_file, lang='zh')
        
        # 添加到主目录页
        main_toc_content += f'            <li><a href="{index_file}">{feed_name}</a></li>\n'
        
        # 确定前后导航
        prev_feed_link = ""
        next_feed_link = ""
        if feed_idx > 0:
            # 获取前一个 feed 的名称
            prev_key, prev_data = feed_list[feed_idx-1]
            if isinstance(prev_data, dict) and 'entries' in prev_data:
                prev_name = prev_data.get('config_name') or prev_data.get('feed_meta', {}).get('title', prev_key)
            else:
                prev_name = prev_key
            prev_feed_file = sanitize_filename(prev_name) + "_toc.xhtml"
            prev_feed_link = f'<a href="{prev_feed_file}">Prev</a>'
        if feed_idx < len(feed_list) - 1:
            # 获取下一个 feed 的名称
            next_key, next_data = feed_list[feed_idx+1]
            if isinstance(next_data, dict) and 'entries' in next_data:
                next_name = next_data.get('config_name') or next_data.get('feed_meta', {}).get('title', next_key)
            else:
                next_name = next_key
            next_feed_file = sanitize_filename(next_name) + "_toc.xhtml"
            next_feed_link = f'<a href="{next_feed_file}">Next</a>'
        
        # 构建导航栏 - 根据上下文调整文字
        nav_parts = []
        has_prev = bool(prev_feed_link)
        has_next = bool(next_feed_link)
        
        if has_prev and has_next:
            # 完整导航: Prev | Main menu | Next
            nav_parts.append(prev_feed_link)
            nav_parts.append('<a href="main_toc.xhtml">Main menu</a>')
            nav_parts.append(next_feed_link)
        elif has_prev and not has_next:
            # 最后一个: Previous | Main menu
            prev_feed_link = prev_feed_link.replace('>Prev<', '>Previous<')
            nav_parts.append(prev_feed_link)
            nav_parts.append('<a href="main_toc.xhtml">Main menu</a>')
        elif not has_prev and has_next:
            # 第一个: Main menu | Next
            nav_parts.append('<a href="main_toc.xhtml">Main menu</a>')
            nav_parts.append(next_feed_link)
        else:
            # 只有一个 feed: Main menu
            nav_parts.append('<a href="main_toc.xhtml">Main menu</a>')
            
        navigation_bar = ' | '.join(nav_parts)
        
        # 获取 feed subtitle
        feed_subtitle = ""
        if 'title_detail' in feed_meta and 'subtitle' in feed_meta.get('title_detail', {}):
            feed_subtitle = feed_meta['title_detail']['subtitle']
        elif 'subtitle' in feed_meta:
            feed_subtitle = feed_meta.get('subtitle', '')
            
        # 构建 feed 索引页内容
        index_content = f'''
        <html xmlns="http://www.w3.org/1999/xhtml">
        <head>
            <title>{feed_name}</title>
            <style>
                a {{ color: black; text-decoration: underline; }}
                .nav {{ margin: 20px 0; padding: 10px; }}
                .description-preview {{ 
                    color: #666; 
                    font-size: 0.9em; 
                    margin-left: 20px; 
                    margin-top: 5px;
                }}
                img {{ 
                    page-break-inside: avoid;
                    break-inside: avoid;
                    display: block;
                    max-width: 100%;
                    height: auto;
                }}
                figure {{
                    page-break-inside: avoid;
                    break-inside: avoid;
                }}
            </style>
        </head>
        <body>
            <center>
                <div class="nav">{navigation_bar}</div>
            </center>
            <hr/>
            <center>
                <h1>{feed_name}</h1>
                {f'<p><i>{feed_subtitle}</i></p>' if feed_subtitle else ''}
            </center>
            <ul>
        '''
        
        # 处理每篇文章
        article_toc = []
        feed_articles = []  # 当前 feed 的文章列表
        
        for idx, entry in enumerate(entries, 1):
            entry_file = f"{sanitize_filename(feed_name)}_{idx:03d}.xhtml"
            
            # 获取发布时间
            pub_date = ""
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                pub_date = datetime(*entry.published_parsed[:6]).strftime('%Y-%m-%d %H:%M')
            elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                pub_date = datetime(*entry.updated_parsed[:6]).strftime('%Y-%m-%d %H:%M')
            
            # 获取描述预览（前100个字符）
            description_preview = ""
            raw_desc = entry.get('summary', entry.get('description', ''))
            if raw_desc:
                # 移除HTML标签
                import re
                clean_desc = re.sub(r'<[^>]+>', '', raw_desc)
                clean_desc = clean_desc.strip()
                if len(clean_desc) > 100:
                    description_preview = clean_desc[:100] + '[...]'
                else:
                    description_preview = clean_desc
            
            # 添加到索引页（使用HTML列表）
            index_content += f'''
                <li>
                    <a href="{entry_file}">{entry.title} - {pub_date}</a>
                    {f'<div class="description-preview">{description_preview}</div>' if description_preview else ''}
                </li>
            '''
            
            # 创建文章页面
            chapter = epub.EpubHtml(title=entry.title, file_name=entry_file, lang='zh')
            
            # 存储文章信息用于导航
            article_info = {
                'chapter': chapter,
                'feed_name': feed_name,
                'index_file': index_file,
                'entry_file': entry_file,
                'title': entry.title,
                'feed_idx': feed_idx
            }
            feed_articles.append(article_info)
            all_articles.append(article_info)
            
            # 获取并处理文章内容
            raw_content = entry.get('summary', entry.get('description', '暂无摘要'))
            
            # 检查是否需要解析原始链接内容
            feed_config = feeds_config.get(feed_name, {}) if feeds_config else {}
            resolve_config = feed_config.get('resolve_link', None)
            if resolve_config and entry.get('link'):
                resolved_content = resolve_link_content(entry.link, resolve_config)
                if resolved_content:
                    # 成功解析，使用解析后的内容
                    raw_content = resolved_content
                    print(f"  ✓ 已解析原始内容: {entry.title[:30]}...")
                else:
                    print(f"  ✗ 无法解析原始内容，使用RSS摘要: {entry.title[:30]}...")
            
            # 处理内容中的图片
            processed_content = raw_content
            if load_images:
                # 提取并替换图片
                img_urls = extract_images_from_html(raw_content)
                for img_url in img_urls:
                    img_counter += 1
                    local_img = download_and_add_image(book, img_url, img_counter)
                    if local_img:
                        # 替换为本地图片路径
                        img_pattern = f'<img[^>]*src=["\']?{re.escape(img_url)}["\']?[^>]*>'
                        new_img = f'<img src="{local_img}" alt="图片"/>'
                        processed_content = re.sub(img_pattern, new_img, processed_content)
            else:
                # 移除所有图片标签
                processed_content = re.sub(r'<img[^>]*>', '', processed_content)
            
            # 暂时保存基本内容，导航将在后面添加
            article_base_content = f'''
                <hr/>
                <center><h1>{entry.title}</h1></center>
                <p>
                    <small>
                        {f'发布时间：{pub_date}' if pub_date else ''}
                        {f'来源：{feed_name}' if feed_name else ''}
                    </small>
                </p>
                <br/>
                <blockquote>
                    {processed_content}
                </blockquote>
            '''
            
            # 处理额外的媒体图片（如果有）
            if load_images:
                extra_images = []
                
                # 收集额外的媒体图片
                if hasattr(entry, 'media_content') and entry.media_content:
                    for media in entry.media_content:
                        if 'url' in media:
                            extra_images.append(media['url'])
                elif hasattr(entry, 'enclosures') and entry.enclosures:
                    for enclosure in entry.enclosures:
                        if enclosure.type and enclosure.type.startswith('image/'):
                            extra_images.append(enclosure.href)
                
                # 如果有额外图片，下载并嵌入
                if extra_images:
                    article_base_content += '<br/><h2>▣ 附加图片</h2>'
                    for img_url in extra_images:
                        img_counter += 1
                        local_img = download_and_add_image(book, img_url, img_counter)
                        if local_img:
                            article_base_content += f'<p><img src="{local_img}" alt="文章配图"/></p>'
                        else:
                            # 如果下载失败，使用原始 URL
                            article_base_content += f'<p><img src="{img_url}" alt="文章配图"/></p>'
            
            # 暂时保存内容，稍后添加导航
            chapter.base_content = article_base_content
            book.add_item(chapter)
            # 不在这里添加到 spine，稍后统一处理
            article_toc.append(chapter)
        
        # 为当前 feed 的文章添加导航
        for i, article_info in enumerate(feed_articles):
            # 构建导航元素
            nav_parts = []
            
            has_prev = i > 0
            has_next = i < len(feed_articles) - 1
            
            # 根据前后文确定导航文字
            if has_prev and has_next:
                # 完整导航: Prev | Sec | Main menu | Next
                nav_parts.append(f'<a href="{feed_articles[i-1]["entry_file"]}">Prev</a>')
                nav_parts.append(f'<a href="{index_file}">Sec</a>')
                nav_parts.append('<a href="main_toc.xhtml">Main menu</a>')
                nav_parts.append(f'<a href="{feed_articles[i+1]["entry_file"]}">Next</a>')
            elif has_prev and not has_next:
                # 最后一篇: Prev | Sec | Main menu
                nav_parts.append(f'<a href="{feed_articles[i-1]["entry_file"]}">Prev</a>')
                nav_parts.append(f'<a href="{index_file}">Sec</a>')
                nav_parts.append('<a href="main_toc.xhtml">Main menu</a>')
            elif not has_prev and has_next:
                # 第一篇: Sec | Main menu | Next
                nav_parts.append(f'<a href="{index_file}">Sec</a>')
                nav_parts.append('<a href="main_toc.xhtml">Main menu</a>')
                nav_parts.append(f'<a href="{feed_articles[i+1]["entry_file"]}">Next</a>')
            else:
                # 只有一篇: Section | Main menu
                nav_parts.append(f'<a href="{index_file}">Section</a>')
                nav_parts.append('<a href="main_toc.xhtml">Main menu</a>')
            
            navigation_bar = ' | '.join(nav_parts)
            
            # 构建完整的文章页面
            article_content = f'''
            <html xmlns="http://www.w3.org/1999/xhtml">
            <head>
                <title>{article_info["title"]}</title>
                <style>
                    a {{ color: black; text-decoration: underline; }}
                    .nav {{ margin: 20px 0; padding: 10px; }}
                    img {{ 
                        page-break-inside: avoid;
                        break-inside: avoid;
                        display: block;
                        max-width: 100%;
                        height: auto;
                    }}
                    figure {{
                        page-break-inside: avoid;
                        break-inside: avoid;
                    }}
                    p {{
                        orphans: 2;
                        widows: 2;
                    }}
                </style>
            </head>
            <body>
                <center>
                    <div class="nav">{navigation_bar}</div>
                </center>
                {article_info['chapter'].base_content}
            </body>
            </html>
            '''
            
            article_info['chapter'].content = article_content
            del article_info['chapter'].base_content
        
        # 完成索引页并添加底部导航
        index_content += f'''
            </ul>
            <hr/>
            <center>
                <div class="nav">{navigation_bar}</div>
            </center>
        </body>
        </html>
        '''
        
        feed_index_page.content = index_content
        book.add_item(feed_index_page)
        book.spine.append(feed_index_page)  # 先添加索引页
        
        # 然后添加该 feed 的所有文章
        for chapter in article_toc:
            book.spine.append(chapter)
        
        # 添加到内置 TOC
        book.toc.append(feed_index_page)
    
    # 完成主目录页
    main_toc_content += '''
        </ul>
    </body>
    </html>
    '''
    main_toc_page.content = main_toc_content
    book.add_item(main_toc_page)
    
    # 添加导航文件
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    
    # 生成文件名
    if custom_filename:
        # 使用自定义文件名，替换日期占位符
        current_date = datetime.now()
        replacements = {
            '{year}': str(current_date.year),
            '{month}': f'{current_date.month:02d}',
            '{day}': f'{current_date.day:02d}',
            '{hour}': f'{current_date.hour:02d}',
            '{minute}': f'{current_date.minute:02d}',
            '{second}': f'{current_date.second:02d}',
            '{date}': f'{current_date.year}年{current_date.month}月{current_date.day}日',
            '{time}': f'{current_date.hour:02d}时{current_date.minute:02d}分',
            '{datetime}': f'{current_date.year}年{current_date.month}月{current_date.day}日_{current_date.hour:02d}时{current_date.minute:02d}分'
        }
        filename = custom_filename
        for placeholder, value in replacements.items():
            filename = filename.replace(placeholder, value)
        
        # 确保文件扩展名为.epub
        if not filename.endswith('.epub'):
            filename += '.epub'
    else:
        # 默认文件名格式
        timestamp = current_date.strftime('%Y%m%d_%H%M%S')
        filename = f'rss_feed_{timestamp}.epub'
    
    # 输出 EPUB
    epub.write_epub(filename, book, {})
    print(f"✅ EPUB 电子书已生成：{filename}")

def main():
    config = load_config()
    all_feeds = {}
    feeds_config = {}  # 存储每个feed的配置
    
    for feed in config['Feeds']:
        if feed.get('enabled', True):
            parsed_feed = fetch_feed(feed['url'])
            entries = filter_entries(parsed_feed.entries, config['Settings'].get('max_history', -1))
            # 保存配置名称和 feed 元数据
            feed_title = feed.get('title', feed.get('name', feed['url']))
            all_feeds[feed_title] = {
                'entries': entries,
                'config_name': feed.get('name'),
                'feed_meta': parsed_feed.feed  # 包含 feed 的元数据
            }
            # 保存feed配置
            feeds_config[feed_title] = feed

    # 获取自定义文件名（如果配置中有）
    custom_filename = config.get('Settings', {}).get('filename_template')
    convert_to_epub(all_feeds, config['Settings'].get('load_images', True), feeds_config, custom_filename)

if __name__ == "__main__":
    main()
