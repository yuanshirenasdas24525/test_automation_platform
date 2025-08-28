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

    @pytest.mark.run(order=1)
    @pytest.mark.parametrize('case', GenericCaseReader(ProjectPaths.register, process_api_row).read())
    def test_uu_apitest_register_case(self, case):
        client.send_case(case=case)

    @pytest.mark.run(order=2)
    @pytest.mark.parametrize('case', GenericCaseReader(ProjectPaths.login, process_api_row).read())
    def test_uu_apitest_login_case(self, case):
        client.send_case(case=case)

    @pytest.mark.run(order=3)
    @pytest.mark.parametrize('case', GenericCaseReader(ProjectPaths.userinfo, process_api_row).read())
    def test_uu_apitest_userinfo_case(self, case):
        client.send_case(case=case)

    @pytest.mark.run(order=4)
    @pytest.mark.parametrize('case', GenericCaseReader(ProjectPaths.security, process_api_row).read())
    def test_uu_apitest_security_case(self, case):
        client.send_case(case=case)

    @pytest.mark.run(order=5)
    @pytest.mark.parametrize('case', GenericCaseReader(ProjectPaths.deposit, process_api_row).read())
    def test_uu_apitest_deposit_case(self, case):
        client.send_case(case=case)

    @pytest.mark.run(order=6)
    @pytest.mark.parametrize('case', GenericCaseReader(ProjectPaths.withdraw, process_api_row).read())
    def test_uu_apitest_withdraw_case(self, case):
        client.send_case(case=case)

    @pytest.mark.run(order=7)
    @pytest.mark.parametrize('case', GenericCaseReader(ProjectPaths.converter, process_api_row).read())
    def test_uu_apitest_converter_case(self, case):
        client.send_case(case=case)

    @pytest.mark.run(order=8)
    @pytest.mark.parametrize('case', GenericCaseReader(ProjectPaths.card, process_api_row).read())
    def test_uu_apitest_card_case(self, case):
        client.send_case(case=case)

    @pytest.mark.run(order=9)
    @pytest.mark.parametrize('case', GenericCaseReader(ProjectPaths.agent, process_api_row).read())
    def test_uu_apitest_agent_case(self, case):
        client.send_case(case=case)

    def teardown_method(self):
        pass

    def teardown_class(self):
        pass
