# 文件名: __init__.py
import os
import re
import time
import pandas as pd
import requests

# 导入 MoviePilot 的核心模块
from app.core.config import settings
from app.log import logger

class MediaLibraryManagerPlugin:
    """
    媒体库管家插件
    """

    # 插件入口，当 MoviePilot 启动或插件配置更新时调用
    def init_plugin(self, config: dict = None):
        self.config = config
        self.plugin_name = "媒体库管家"
        
        # 从配置中获取参数
        self.movie_path = self.config.get('movie_path')
        self.tv_path = self.config.get('tv_path')
        self.tmdb_api_key = self.config.get('tmdb_api_key')
        self.use_proxy = self.config.get('use_proxy')
        self.proxy_url = self.config.get('proxy_url')
        
        # 定义插件使用的数据文件路径，存放在 MoviePilot 的 data 目录下
        self.data_path = os.path.join(settings.DATA_PATH, "plugins", "medialibmanager")
        os.makedirs(self.data_path, exist_ok=True) # 确保目录存在
        
        self.movie_inventory_file = os.path.join(self.data_path, 'inventory_movies.xlsx')
        self.tv_inventory_file = os.path.join(self.data_path, 'inventory_tv.xlsx')
        self.enriched_movie_file = os.path.join(self.data_path, 'enriched_movies.xlsx')
        self.enriched_tv_file = os.path.join(self.data_path, 'enriched_tv.xlsx')
        self.master_file = os.path.join(self.data_path, '_MASTER_inventory.xlsx')
        self.delete_list_file = os.path.join(self.data_path, 'files_to_delete.txt')
        
        logger.info(f"{self.plugin_name}：插件已加载，数据目录位于 {self.data_path}")

    # 响应 "扫描并生成总表" 按钮
    def run_scan(self):
        logger.info(f"{self.plugin_name}：开始执行扫描与分析...")
        
        if not self.tmdb_api_key:
            logger.error(f"{self.plugin_name}：TMDb API 密钥未配置，操作中止。")
            return
            
        # 1. 盘点
        if self.movie_path:
            self._generate_inventory(self.movie_path, 'movie')
        if self.tv_path:
            self._generate_inventory(self.tv_path, 'tv')
            
        # 2. 丰富信息
        if os.path.exists(self.movie_inventory_file):
            self._enrich_inventory(self.movie_inventory_file, 'movie')
        if os.path.exists(self.tv_inventory_file):
            self._enrich_inventory(self.tv_inventory_file, 'tv')
        
        # 3. 合并
        self._combine_inventories()
        
        logger.info(f"{self.plugin_name}：扫描与分析完成！")
        logger.info(f"{self.plugin_name}：请访问路径 {self.master_file}，编辑总表并标记要删除的文件。")
        return "扫描与分析完成，请查看日志和插件数据目录中的 `_MASTER_inventory.xlsx` 文件。"

    # 响应 "执行删除" 按钮
    def run_delete(self):
        logger.warning(f"{self.plugin_name}：【危险操作】即将开始执行删除...")
        
        if not os.path.exists(self.master_file):
            logger.error(f"{self.plugin_name}：找不到总表 {self.master_file}，请先执行扫描。")
            return "找不到总表，请先执行扫描。"
            
        # 4. 创建删除列表
        self._create_deletion_list()
        
        # 5. 执行删除
        result = self._execute_deletion()
        
        logger.info(f"{self.plugin_name}：删除操作执行完毕。")
        return result
        
    """
    ============================================================
    以下为从脚本重构而来的内部核心逻辑方法
    ============================================================
    """

    def _generate_inventory(self, base_dir, media_type):
        logger.info(f"开始盘点 {media_type} 目录：{base_dir}")
        if not os.path.isdir(base_dir):
            logger.error(f"目录 '{base_dir}' 不存在。")
            return

        inventory_data = []
        video_extensions = ('.mkv', '.mp4', '.avi', '.ts', '.rmvb', '.mov')
        
        for root, dirs, files in os.walk(base_dir):
            for file_name in files:
                if file_name.lower().endswith(video_extensions):
                    folder_path = os.path.dirname(os.path.join(root, file_name))
                    folder_name = os.path.basename(folder_path)
                    
                    if media_type == 'movie':
                        base_title, year = self._get_movie_base_name(folder_name)
                    else: # tv
                        base_title, year = self._get_tv_show_base_name(folder_name)
                        
                    file_path = os.path.join(root, file_name)
                    file_size_gb = round(os.path.getsize(file_path) / (1024**3), 2)
                    
                    inventory_data.append({
                        'SearchTitle': base_title,
                        'SearchYear': year,
                        'FilePath': file_path,
                        'FileName': file_name,
                        'FileSizeGB': file_size_gb,
                        'FolderPath': folder_path
                    })

        if not inventory_data:
            logger.warning(f"{media_type} 目录扫描完成，但未找到视频文件。")
            return

        df = pd.DataFrame(inventory_data)
        output_file = self.movie_inventory_file if media_type == 'movie' else self.tv_inventory_file
        df.to_excel(output_file, index=False)
        logger.info(f"{media_type} 清单已生成：{output_file}")

    def _enrich_inventory(self, inventory_path, media_type):
        logger.info(f"开始为 {media_type} 清单丰富TMDb信息...")
        df = pd.read_excel(inventory_path)
        enriched_data = []
        
        unique_items = df.drop_duplicates(subset=['SearchTitle', 'SearchYear'])
        logger.info(f"共找到 {len(unique_items)} 部独立的 {media_type} 需要获取信息...")

        item_details_cache = {}
        for index, row in unique_items.iterrows():
            logger.info(f"处理中 ({index + 1}/{len(unique_items)}): {row['SearchTitle']}")
            details = self._get_tmdb_details(row['SearchTitle'], row['SearchYear'], media_type)
            item_details_cache[(row['SearchTitle'], row['SearchYear'])] = details
            time.sleep(0.1)

        logger.info("信息获取完成，正在匹配到所有文件行...")
        for index, row in df.iterrows():
            details = item_details_cache.get((row['SearchTitle'], row['SearchYear']), {})
            new_data = row.to_dict()
            if details:
                if media_type == 'movie':
                    new_data.update(self._parse_movie_details(details))
                else: # tv
                    new_data.update(self._parse_tv_details(details))
            enriched_data.append(new_data)
        
        enriched_df = pd.DataFrame(enriched_data)
        output_file = self.enriched_movie_file if media_type == 'movie' else self.enriched_tv_file
        enriched_df.to_excel(output_file, index=False)
        logger.info(f"丰富的 {media_type} 清单已生成：{output_file}")

    def _combine_inventories(self):
        logger.info("开始合并电影和电视剧清单...")
        has_movies = os.path.exists(self.enriched_movie_file)
        has_tv = os.path.exists(self.enriched_tv_file)

        if not has_movies and not has_tv:
            logger.warning("没有找到已处理的电影或电视剧清单，合并操作跳过。")
            return

        df_movies = pd.DataFrame()
        if has_movies:
            df_movies = pd.read_excel(self.enriched_movie_file)
            df_movies['Type'] = 'Movie'
            df_movies.rename(columns={'TMDb_Title': 'TMDb_Name', 'ReleaseDate': 'AirDate', 'Runtime_Minutes': 'Runtime'}, inplace=True)

        df_tv = pd.DataFrame()
        if has_tv:
            df_tv = pd.read_excel(self.enriched_tv_file)
            df_tv['Type'] = 'TV Show'
            df_tv.rename(columns={'FirstAirDate': 'AirDate'}, inplace=True)
            
        df_combined = pd.concat([df_movies, df_tv], ignore_index=True)
        df_combined.to_excel(self.master_file, index=False)
        logger.info(f"合并成功！总清单已生成：{self.master_file}")

    def _create_deletion_list(self):
        logger.info("正在根据总表生成待删除文件列表...")
        df = pd.read_excel(self.master_file)
        if 'Action' not in df.columns:
            logger.error("总表中缺少 'Action' 列。请添加此列并标记要删除的文件。")
            return
            
        delete_df = df[df['Action'].astype(str).str.upper() == 'DELETE']
        if delete_df.empty:
            logger.info("在 'Action' 列中没有找到任何标记为 'DELETE' 的项。")
            return
            
        files_to_delete = delete_df['FilePath'].tolist()
        with open(self.delete_list_file, 'w', encoding='utf-8') as f:
            for path in files_to_delete:
                f.write(path + "\n")
        logger.info(f"找到 {len(files_to_delete)} 个待删除文件，列表已生成：{self.delete_list_file}")

    def _execute_deletion(self):
        logger.info("开始执行删除操作...")
        if not os.path.exists(self.delete_list_file):
            logger.warning("未找到待删除文件列表，操作中止。")
            return "未找到待删除文件列表，操作中止。"
            
        with open(self.delete_list_file, 'r', encoding='utf-8') as f:
            files_to_delete = [line.strip() for line in f if line.strip()]
        
        if not files_to_delete:
            logger.info("待删除列表为空，无需任何操作。")
            return "待删除列表为空，无需任何操作。"
            
        deleted_count = 0
        for f_path in files_to_delete:
            try:
                if os.path.exists(f_path) and os.path.isfile(f_path):
                    os.remove(f_path)
                    logger.info(f"[已删除] {f_path}")
                    deleted_count += 1
                else:
                    logger.warning(f"[跳过] 文件已不存在: {f_path}")
            except Exception as e:
                logger.error(f"[删除失败] {f_path} -> 错误: {e}")
                
        # 清理工作
        os.remove(self.delete_list_file)
        
        result_message = f"操作完成！共成功删除 {deleted_count} 个文件。"
        logger.info(result_message)
        return result_message

    # -- TMDb API & 解析辅助方法 --
    
    def _get_proxies(self):
        if self.use_proxy and self.proxy_url:
            return {"http": self.proxy_url, "https": self.proxy_url}
        return None

    def _get_tmdb_details(self, title, year, media_type):
        api_path = 'tv' if media_type == 'tv' else 'movie'
        headers = {"accept": "application/json", "Authorization": f"Bearer {self.tmdb_api_key}"}
        
        search_url = f"https://api.themoviedb.org/3/search/{api_path}?query={requests.utils.quote(title)}&language=zh-CN"
        if year:
            year_param = 'first_air_date_year' if media_type == 'tv' else 'year'
            search_url += f"&{year_param}={year}"
            
        try:
            response = requests.get(search_url, headers=headers, proxies=self._get_proxies())
            response.raise_for_status()
            results = response.json().get('results', [])
            if not results:
                return {}
            
            item_id = results[0]['id']
            details_url = f"https://api.themoviedb.org/3/{api_path}/{item_id}?language=zh-CN"
            response = requests.get(details_url, headers=headers, proxies=self._get_proxies())
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"请求TMDb API时出错 (Title: {title}): {e}")
            return {}
            
    def _parse_movie_details(self, details):
        return {
            'TMDb_Title': details.get('title', 'N/A'),
            'TMDb_Rating': details.get('vote_average', 0),
            'ReleaseDate': details.get('release_date', 'N/A'),

            'Genres': ', '.join([g['name'] for g in details.get('genres', [])]),
            'Runtime_Minutes': details.get('runtime', 0),
            'ProductionCountries': ', '.join([c['name'] for c in details.get('production_countries', [])]),
            'Overview': details.get('overview', 'N/A')
        }

    def _parse_tv_details(self, details):
        return {
            'TMDb_Name': details.get('name', 'N/A'),
            'TMDb_Rating': details.get('vote_average', 0),
            'FirstAirDate': details.get('first_air_date', 'N/A'),
            'Genres': ', '.join([g['name'] for g in details.get('genres', [])]),
            'SeasonsCount': details.get('number_of_seasons', 0),
            'EpisodesCount': details.get('number_of_episodes', 0),
            'Overview': details.get('overview', 'N/A')
        }
        
    def _get_movie_base_name(self, text):
        match = re.match(r'^(.*?)\s*\((\d{4})\)', text)
        if match:
            return match.group(1).strip().replace('.', ' ').strip(), match.group(2)
        return text.strip().replace('.', ' ').strip(), None

    def _get_tv_show_base_name(self, text):
        match = re.match(r'^(.*?)\s*\((\d{4})\)', text)
        if match:
            return match.group(1).strip().replace('.', ' ').strip(), match.group(2)
        cleaned_title = re.sub(r'[\s\.]S\d{1,2}(E\d{1,2})?.*', '', text, flags=re.IGNORECASE).strip()
        cleaned_title = re.sub(r'[\s\.]Season[\s\.]\d{1,2}.*', '', cleaned_title, flags=re.IGNORECASE).strip()
        return cleaned_title.replace('.', ' ').strip(), None