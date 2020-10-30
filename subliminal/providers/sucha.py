# -*- coding: utf-8 -*-
import io
import logging
import os
import zipfile

import rarfile
from babelfish import Language
from guessit import guessit
from requests import Session

from ..exceptions import ProviderError
from ..matches import guess_matches
from ..subtitle import Subtitle, fix_line_ending
from ..video import Episode
from . import Provider

logger = logging.getLogger(__name__)

server_url = "http://sapi.caretas.club/"
page_url = "https://sucha.caretas.club/"


class SuchaSubtitle(Subtitle):
    provider_name = "sucha"
    hash_verifiable = False

    def __init__(
        self,
        language,
        page_link,
        filename,
        download_link,
        hearing_impaired,
        matches,
        is_episode,
    ):
        super(SuchaSubtitle, self).__init__(
            language, hearing_impaired=hearing_impaired, page_link=page_url
        )
        self.download_link = download_link
        self.is_episode = is_episode
        self.referer = page_link
        self.language = language
        self.release_info = filename
        self.filename = filename
        self.found_matches = matches

    @property
    def id(self):
        return self.download_link

    def get_matches(self, video):
        if self.is_episode:
            self.found_matches |= guess_matches(
                video, guessit(self.filename, {"type": "episode"})
            )
        else:
            self.found_matches |= guess_matches(
                video, guessit(self.filename, {"type": "movie"})
            )
        return self.found_matches


class SuchaProvider(Provider):
    """Sucha Provider"""

    languages = {Language.fromalpha2(l) for l in ["es"]}
    language_list = list(languages)
    logger.debug(languages)

    def initialize(self):
        self.session = Session()
        self.session.headers = {
            "User-Agent": os.environ.get("SZ_USER_AGENT", "Sub-Zero/2")
        }

    def terminate(self):
        self.session.close()

    def query(self, languages, video):
        movie_year = video.year if video.year else None
        is_episode = True if isinstance(video, Episode) else False
        imdb_id = video.imdb_id if video.imdb_id else None
        language = self.language_list[0]
        if is_episode:
            q = {
                "query": "{} S{:02}E{:02}".format(
                    video.series, video.season, video.episode
                )
            }
        else:
            if imdb_id:
                q = {"query": imdb_id}
            else:
                q = {"query": video.title, "year": movie_year}

        logger.debug("Searching subtitles: {}".format(q["query"]))

        res = self.session.get(server_url + "search", params=q, timeout=10)
        res.raise_for_status()
        result = res.json()

        try:
            subtitles = []
            for i in result["results"]:
                logger.debug(video.audio_codec)
                logger.debug(video.video_codec)
                matches = set()
                logger.debug(i["title"])
                if video.title.lower() in i["title"].lower():
                    matches.add("title")
                    matches.add("country")
                if is_episode:
                    if q["query"].lower() == i["title"].lower():
                        matches.add("title")
                        matches.add("series")
                        matches.add("season")
                        matches.add("episode")
                        matches.add("year")
                if i["year"] == str(video.year):
                    matches.add("year")
                if imdb_id:
                    matches.add("imdb_id")

                filename = i["pseudo_file"]
                if (
                    video.release_group
                    and str(video.release_group).lower() in i["original_description"]
                ):
                    filename = i["pseudo_file"].replace(
                        ".es.srt", "-" + str(video.release_group) + ".es.srt"
                    )

                subtitles.append(
                    SuchaSubtitle(
                        language,
                        i["referer"],
                        filename,
                        i["download_url"],
                        i["hearing_impaired"],
                        matches,
                        is_episode,
                    )
                )

            return subtitles
        except KeyError:
            logger.debug("No subtitles found")
            return []

    def list_subtitles(self, video, languages):
        return self.query(languages, video)

    def _check_response(self, response):
        if response.status_code != 200:
            raise ProviderError("Bad status code: " + str(response.status_code))

    def _get_archive(self, content):
        archive_stream = io.BytesIO(content)
        if rarfile.is_rarfile(archive_stream):
            logger.debug("Identified rar archive")
            archive = rarfile.RarFile(archive_stream)
        elif zipfile.is_zipfile(archive_stream):
            logger.debug("Identified zip archive")
            archive = zipfile.ZipFile(archive_stream)
        else:
            raise ValueError("Unsupported compressed format")
        return archive

    def get_file(self, archive):
        for name in archive.namelist():
            if os.path.split(name)[-1].startswith("."):
                continue
            if not name.lower().endswith(".srt"):
                continue
            if (
                "[eng]" in name.lower()
                or ".en." in name.lower()
                or ".eng." in name.lower()
            ):
                continue
            logger.debug("Returning from archive: {}".format(name))
            return archive.read(name)
        raise ValueError("Can not find the subtitle in the compressed file")

    def download_subtitle(self, subtitle):
        logger.info("Downloading subtitle %r", subtitle)
        response = self.session.get(
            subtitle.download_link, headers={"Referer": subtitle.page_link}, timeout=10
        )
        response.raise_for_status()
        self._check_response(response)
        archive = self._get_archive(response.content)
        subtitle_file = self.get_file(archive)
        subtitle.content = fix_line_ending(subtitle_file)
