import binascii

from zope.interface import implements

from lbrynet.lbry_file.StreamDescriptor import save_sd_info
from lbrynet.cryptstream.client.CryptStreamDownloader import CryptStreamDownloader
from lbrynet.core.client.StreamProgressManager import FullStreamProgressManager
from lbrynet.interfaces import IStreamDownloaderFactory
from lbrynet.lbry_file.client.EncryptedFileMetadataHandler import EncryptedFileMetadataHandler
import os
from twisted.internet import defer, threads
import logging
import traceback


log = logging.getLogger(__name__)


class EncryptedFileDownloader(CryptStreamDownloader):
    """Classes which inherit from this class download LBRY files"""

    def __init__(self, stream_hash, peer_finder, rate_limiter, blob_manager,
                 storage, payment_rate_manager, wallet, key, stream_name,
                 suggested_file_name=None):
        CryptStreamDownloader.__init__(self, peer_finder, rate_limiter, blob_manager,
                                       payment_rate_manager, wallet, key, stream_name)
        self.stream_hash = stream_hash
        self.storage = storage
        self.suggested_file_name = binascii.unhexlify(suggested_file_name)
        self._calculated_total_bytes = None

    @defer.inlineCallbacks
    def delete_data(self):
        crypt_infos = yield self.storage.get_blobs_for_stream(self.stream_hash)
        blob_hashes = [b.blob_hash for b in crypt_infos]
        sd_hash = yield self.storage.get_sd_blob_hash_for_stream(self.stream_hash)
        blob_hashes.append(sd_hash)
        yield self.blob_manager.delete_blobs(blob_hashes)

    def stop(self, err=None):
        d = self._close_output()
        d.addCallback(lambda _: CryptStreamDownloader.stop(self, err=err))
        return d

    def _get_progress_manager(self, download_manager):
        return FullStreamProgressManager(self._finished_downloading,
                                         self.blob_manager, download_manager)

    def _start(self):
        d = self._setup_output()
        d.addCallback(lambda _: CryptStreamDownloader._start(self))
        return d

    def _setup_output(self):
        pass

    def _close_output(self):
        pass

    def get_total_bytes(self):
        d = self.storage.get_blobs_for_stream(self.stream_hash)

        def calculate_size(blobs):
            return sum([b.length for b in blobs])

        d.addCallback(calculate_size)
        return d

    def get_total_bytes_cached(self):
        if self._calculated_total_bytes is None or self._calculated_total_bytes == 0:
            if self.download_manager is None:
                return 0
            else:
                self._calculated_total_bytes = self.download_manager.calculate_total_bytes()
        return self._calculated_total_bytes

    def get_bytes_left_to_output(self):
        if self.download_manager is not None:
            return self.download_manager.calculate_bytes_left_to_output()
        else:
            return 0

    def get_bytes_left_to_download(self):
        if self.download_manager is not None:
            return self.download_manager.calculate_bytes_left_to_download()
        else:
            return 0

    def _get_metadata_handler(self, download_manager):
        return EncryptedFileMetadataHandler(self.stream_hash,
                                            self.storage, download_manager)


class EncryptedFileDownloaderFactory(object):
    implements(IStreamDownloaderFactory)

    def __init__(self, peer_finder, rate_limiter, blob_manager, stream_info_manager,
                 wallet):
        self.peer_finder = peer_finder
        self.rate_limiter = rate_limiter
        self.blob_manager = blob_manager
        self.stream_info_manager = stream_info_manager
        self.wallet = wallet

    def can_download(self, sd_validator):
        return True

    def make_downloader(self, metadata, options, payment_rate_manager, **kwargs):
        assert len(options) == 1
        data_rate = options[0]
        payment_rate_manager.min_blob_data_payment_rate = data_rate

        def save_source_if_blob(stream_hash):
            return defer.succeed(metadata.source_blob_hash)

        def create_downloader(stream_hash):
            downloader = self._make_downloader(stream_hash, payment_rate_manager,
                                               metadata.validator.raw_info)
            d = downloader.set_stream_info()
            d.addCallback(lambda _: downloader)
            return d

        d = save_sd_info(self.blob_manager, metadata.source_blob_hash, metadata.validator.raw_info)
        d.addCallback(save_source_if_blob)
        d.addCallback(create_downloader)
        return d

    def _make_downloader(self, stream_hash, payment_rate_manager, stream_info):
        pass


class EncryptedFileSaver(EncryptedFileDownloader):
    def __init__(self, stream_hash, peer_finder, rate_limiter, blob_manager, stream_info_manager,
                 payment_rate_manager, wallet, download_directory, key, stream_name,
                 suggested_file_name):
        EncryptedFileDownloader.__init__(self, stream_hash, peer_finder, rate_limiter,
                                         blob_manager, stream_info_manager, payment_rate_manager,
                                         wallet, key, stream_name, suggested_file_name)
        self.download_directory = download_directory
        self.file_name = os.path.basename(self.suggested_file_name)
        self.file_written_to = None
        self.file_handle = None

    def __str__(self):
        if self.file_written_to is not None:
            return str(self.file_written_to)
        else:
            return str(self.file_name)

    def stop(self, err=None):
        d = EncryptedFileDownloader.stop(self, err=err)
        d.addCallback(lambda _: self._delete_from_info_manager())
        return d

    def _get_progress_manager(self, download_manager):
        return FullStreamProgressManager(self._finished_downloading,
                                         self.blob_manager,
                                         download_manager)

    def _setup_output(self):
        def open_file():
            if self.file_handle is None:
                file_name = self.file_name
                if not file_name:
                    file_name = "_"
                if os.path.exists(os.path.join(self.download_directory, file_name)):
                    ext_num = 1

                    def _get_file_name(ext):
                        if len(file_name.split(".")):
                            fn = ''.join(file_name.split(".")[:-1])
                            file_ext = ''.join(file_name.split(".")[-1])
                            return fn + "-" + str(ext) + "." + file_ext
                        else:
                            return file_name + "_" + str(ext)

                    while os.path.exists(os.path.join(self.download_directory,
                                                      _get_file_name(ext_num))):
                        ext_num += 1

                    file_name = _get_file_name(ext_num)
                try:
                    self.file_handle = open(os.path.join(self.download_directory, file_name), 'wb')
                    self.file_written_to = os.path.join(self.download_directory, file_name)
                except IOError:
                    log.error(traceback.format_exc())
                    raise ValueError(
                        "Failed to open %s. Make sure you have permission to save files to that"
                        " location." %
                        os.path.join(self.download_directory, file_name))
        return threads.deferToThread(open_file)

    def _close_output(self):
        self.file_handle, file_handle = None, self.file_handle

        def close_file():
            if file_handle is not None:
                name = file_handle.name
                file_handle.close()
                if self.completed is False:
                    os.remove(name)

        return threads.deferToThread(close_file)

    def _get_write_func(self):
        def write_func(data):
            if self.stopped is False and self.file_handle is not None:
                self.file_handle.write(data)
        return write_func

    def _delete_from_info_manager(self):
        return self.stream_info_manager.delete_stream(self.stream_hash)


class EncryptedFileSaverFactory(EncryptedFileDownloaderFactory):
    def __init__(self, peer_finder, rate_limiter, blob_manager, stream_info_manager,
                 wallet, download_directory):
        EncryptedFileDownloaderFactory.__init__(self, peer_finder, rate_limiter, blob_manager,
                                           stream_info_manager, wallet)
        self.download_directory = download_directory

    def _make_downloader(self, stream_hash, payment_rate_manager, stream_info):
        stream_name = stream_info.raw_info['stream_name']
        key = stream_info.raw_info['key']
        suggested_file_name = stream_info.raw_info['suggested_file_name']
        return EncryptedFileSaver(stream_hash, self.peer_finder, self.rate_limiter,
                                  self.blob_manager, self.stream_info_manager,
                                  payment_rate_manager, self.wallet, self.download_directory,
                                  key=key, stream_name=stream_name,
                                  suggested_file_name=suggested_file_name)

    @staticmethod
    def get_description():
        return "Save"
