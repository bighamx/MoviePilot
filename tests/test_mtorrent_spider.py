# -*- coding: utf-8 -*-
import asyncio

import pytest

from app.modules.indexer.spider import mtorrent as mtorrent_module
from app.modules.indexer.spider.mtorrent import MTorrentSpider
from app.schemas import MediaType


class _FakeResponse:
    """构造 M-Team 搜索测试使用的最小响应对象。"""

    def __init__(self, results: list, status_code: int = 200):
        self.status_code = status_code
        self._results = results

    def json(self) -> dict:
        """返回 M-Team 搜索接口结构。"""
        return {"data": {"data": self._results}}


def _build_indexer() -> dict:
    """构造 M-Team API Spider 所需的最小站点配置。"""
    return {
        "id": "mteam",
        "name": "馒头",
        "domain": "https://xp.m-team.io/",
        "apikey": "mteam-secret",
        "ua": "MoviePilot-Test",
        "proxy": False,
    }


@pytest.fixture()
def mteam_spider(monkeypatch):
    """构造不依赖真实数据库配置的 MTorrentSpider。"""
    monkeypatch.setattr(mtorrent_module, "SystemConfigOper", lambda: None)
    return MTorrentSpider(_build_indexer())


def test_music_search_uses_music_categories(mteam_spider):
    """音乐搜索应只提交馒头音乐分区分类，而不是电影分类。"""
    params = mteam_spider._MTorrentSpider__get_params("周杰伦 七里香", MediaType.MUSIC)

    assert params["categories"] == MTorrentSpider._music_category


def test_movie_search_keeps_movie_categories(mteam_spider):
    """电影搜索行为不受音乐分类新增影响。"""
    params = mteam_spider._MTorrentSpider__get_params("流浪地球", MediaType.MOVIE)

    assert params["categories"] == MTorrentSpider._movie_category
    assert params["mode"] == "normal"


def test_adult_search_uses_video_categories(mteam_spider):
    """成人区请求只提交影视分类，并显式使用 adult 模式。"""
    params = mteam_spider._MTorrentSpider__get_params(
        "TEST-001", MediaType.MOVIE, mode="adult"
    )

    assert params["mode"] == "adult"
    assert params["categories"] == [
        "410", "424", "437", "431", "429", "430",
        "426", "432", "436", "440", "425", "412",
    ]


def test_parse_result_marks_music_torrents(mteam_spider):
    """音乐分区种子应标记为音乐媒体类型，供音乐搜索链路筛选。"""
    results = mteam_spider._MTorrentSpider__parse_result([
        {"id": "1", "name": "周杰伦 - 七里香 [FLAC]", "category": "434", "size": "1024", "status": {}},
        {"id": "2", "name": "周杰伦演唱会", "category": "406", "size": "1024", "status": {}},
        {"id": "3", "name": "流浪地球 2160p", "category": "419", "size": "1024", "status": {}},
        {"id": "4", "name": "其他资源", "category": "999", "size": "1024", "status": {}},
    ])

    assert [torrent["category"] for torrent in results] == [
        MediaType.MUSIC.value,
        MediaType.MUSIC.value,
        MediaType.MOVIE.value,
        MediaType.UNKNOWN.value,
    ]


def test_parse_result_marks_adult_video_as_movie(mteam_spider):
    """成人影视分类应标记为电影，非视频成人分类仍保持未知。"""
    raw_results = [
        {"id": category, "name": f"Adult Video {category}",
         "category": category, "size": "1024", "status": {}}
        for category in MTorrentSpider._adult_movie_category
    ]
    raw_results.extend([
        {"id": "2", "name": "Adult Game", "category": "411",
         "size": "1024", "status": {}},
        {"id": "3", "name": "Adult Comic", "category": "413",
         "size": "1024", "status": {}},
        {"id": "4", "name": "Adult Images", "category": "433",
         "size": "1024", "status": {}},
    ])
    results = mteam_spider._MTorrentSpider__parse_result(raw_results)

    assert [torrent["category"] for torrent in results] == (
        [MediaType.MOVIE.value] * len(MTorrentSpider._adult_movie_category) + [
        MediaType.UNKNOWN.value,
        MediaType.UNKNOWN.value,
        MediaType.UNKNOWN.value,
        ]
    )
    assert [torrent["adult"] for torrent in results] == (
        [True] * len(MTorrentSpider._adult_movie_category) + [False, False, False]
    )


def test_movie_search_merges_normal_and_adult_results(mteam_spider, monkeypatch):
    """电影关键字搜索应查询两个分区，并按种子详情链接去重合并。"""
    payloads = []

    def fake_post_res(_request, url: str, json: dict = None, **_kwargs):
        del url
        payloads.append(json)
        if json["mode"] == "normal":
            return _FakeResponse([
                {"id": "1", "name": "Normal Movie", "category": "419",
                 "size": "1024", "status": {}},
            ])
        return _FakeResponse([
            {"id": "1", "name": "Duplicate", "category": "410",
             "size": "1024", "status": {}},
            {"id": "2", "name": "Adult Movie", "category": "429",
             "size": "1024", "status": {}},
        ])

    monkeypatch.setattr(mtorrent_module.RequestUtils, "post_res", fake_post_res)

    error, torrents = mteam_spider.search("Movie", MediaType.MOVIE)

    assert not error
    assert [payload["mode"] for payload in payloads] == ["normal", "adult"]
    assert [torrent["title"] for torrent in torrents] == [
        "Normal Movie", "Adult Movie"
    ]
    assert [torrent["category"] for torrent in torrents] == [
        MediaType.MOVIE.value,
        MediaType.MOVIE.value,
    ]


def test_untyped_keyword_search_queries_normal_and_adult_modes(
        mteam_spider, monkeypatch):
    """未指定媒体类型的关键字搜索也应覆盖普通区和成人影视区。"""
    payloads = []

    def fake_post_res(_request, url: str, json: dict = None, **_kwargs):
        del url
        payloads.append(json)
        return _FakeResponse([])

    monkeypatch.setattr(mtorrent_module.RequestUtils, "post_res", fake_post_res)

    error, torrents = mteam_spider.search("Movie")

    assert not error
    assert torrents == []
    assert [payload["mode"] for payload in payloads] == ["normal", "adult"]
    assert payloads[0]["categories"] == []
    assert payloads[1]["categories"] == MTorrentSpider._adult_movie_category


@pytest.mark.parametrize("keyword,mtype", [
    (None, None),
    ("Series", MediaType.TV),
    ("Artist", MediaType.MUSIC),
])
def test_non_movie_search_only_queries_normal_mode(
        mteam_spider, monkeypatch, keyword, mtype):
    """首页浏览、电视剧和音乐搜索不得额外请求成人区。"""
    payloads = []

    def fake_post_res(_request, url: str, json: dict = None, **_kwargs):
        del url
        payloads.append(json)
        return _FakeResponse([])

    monkeypatch.setattr(mtorrent_module.RequestUtils, "post_res", fake_post_res)

    error, torrents = mteam_spider.search(keyword, mtype)

    assert not error
    assert torrents == []
    assert [payload["mode"] for payload in payloads] == ["normal"]


def test_movie_search_keeps_partial_success(mteam_spider, monkeypatch):
    """普通区失败但成人区成功时应返回成人区结果，不误判站点整体失败。"""
    def fake_post_res(_request, url: str, json: dict = None, **_kwargs):
        del url
        if json["mode"] == "normal":
            return None
        return _FakeResponse([
            {"id": "2", "name": "Adult Movie", "category": "432",
             "size": "1024", "status": {}},
        ])

    monkeypatch.setattr(mtorrent_module.RequestUtils, "post_res", fake_post_res)

    error, torrents = mteam_spider.search("Movie", MediaType.MOVIE)

    assert not error
    assert [torrent["title"] for torrent in torrents] == ["Adult Movie"]


def test_async_movie_search_queries_both_modes(mteam_spider, monkeypatch):
    """异步电影搜索应并发查询普通区和成人区并合并结果。"""
    payloads = []

    async def fake_post_res(_request, url: str, json: dict = None, **_kwargs):
        del url
        payloads.append(json)
        return _FakeResponse([
            {
                "id": json["mode"],
                "name": json["mode"],
                "category": "419" if json["mode"] == "normal" else "410",
                "size": "1024",
                "status": {},
            },
        ])

    monkeypatch.setattr(mtorrent_module.AsyncRequestUtils, "post_res", fake_post_res)

    error, torrents = asyncio.run(
        mteam_spider.async_search("Movie", MediaType.MOVIE)
    )

    assert not error
    assert {payload["mode"] for payload in payloads} == {"normal", "adult"}
    assert [torrent["title"] for torrent in torrents] == ["normal", "adult"]
