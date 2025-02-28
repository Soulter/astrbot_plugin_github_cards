import re
# import os
import uuid
# from PIL import Image
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
# from astrbot.core.utils.io import download_file

GITHUB_URL_PATTERN = r"https://github\.com/[\w\-]+/[\w\-]+(?:/(pull|issues)/\d+)?"
GITHUB_REPO_OPENGRAPH = "https://opengraph.githubassets.com/{hash}/{appendix}"

STAR_HISTORY_URL = "https://api.star-history.com/svg?repos={identifier}&type=Date"

@register("astrbot_plugin_github_cards", "Soulter", "根据群聊中 GitHub 相关链接自动发送 GitHub OpenGraph 图片", "1.0.0", "https://github.com/Soulter/astrbot_plugin_github_cards")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)


    @filter.regex(GITHUB_URL_PATTERN)
    async def github_repo(self, event: AstrMessageEvent):
        '''解析 Github 仓库信息'''
        msg = event.message_str
        match = re.search(GITHUB_URL_PATTERN, msg)
        repo_url = match.group(0)
        repo_url = repo_url.replace("https://github.com/", "")
        hash_value = uuid.uuid4().hex
        opengraph_url = GITHUB_REPO_OPENGRAPH.format(hash=hash_value, appendix=repo_url)
        logger.info(f"生成的 OpenGraph URL: {opengraph_url}")

        try:
            yield event.image_result(opengraph_url)
        except Exception as e:
            logger.error(f"下载图片失败: {e}")
            yield event.plain_result("下载 GitHub 图片失败: " + str(e))
            return
    
    # TODO: svg2png
    # @filter.command("ghstar")
    # async def ghstar(self, event: AstrMessageEvent, identifier: str):
    #     '''查看 GitHub 仓库的 Star 趋势图。如: /ghstar Soulter/AstrBot'''
    #     url = STAR_HISTORY_URL.format(identifier=identifier)
    #     # download svg
    #     fpath = "data/temp/{identifier}.svg".format(identifier=identifier.replace("/",
    #         "_"))
    #     await download_file(url, fpath)
    #     # convert to png
    #     png_fpath = fpath.replace(".svg", ".png")
    #     cairosvg.svg2png(url=fpath, write_to=png_fpath)
    #     # send image
    #     yield event.image_result(png_fpath)
