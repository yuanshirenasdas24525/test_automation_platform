# coding: utf-8
import pytest
from src.utils.read_file import GenericCaseReader, process_api_row
from src.core.api.api_client import client
from config.settings import ProjectPaths


class TestApi(object):

    def setup_class(self):
        pass

    def setup_method(self):
        pass

    @pytest.mark.parametrize('case', GenericCaseReader(ProjectPaths.UU_API, process_api_row).read())
    def test_uu_apitest_case(self, case):
        client.send_case(case=case)

    def teardown_method(self):
        pass

    def teardown_class(self):
        pass
