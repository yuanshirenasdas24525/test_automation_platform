
import pytest
from config.settings import ProjectPaths
from src.core.mobile.start_app import AppManager
from src.utils.read_file import GenericCaseReader, process_ui_row



class TestUiAutomatic:

    def setup_class(self):
        self.app = AppManager().get_app()

    def setup_method(self):
        pass

    @pytest.mark.run(order=1)
    @pytest.mark.parametrize('cases', GenericCaseReader(ProjectPaths.ui_register_case, process_ui_row).read())
    def test_android_register_case(self, cases):
        self.app.app_steps(cases)

    @pytest.mark.run(order=2)
    @pytest.mark.parametrize('cases', GenericCaseReader(ProjectPaths.ui_login_case, process_ui_row).read())
    def test_android_login_case(self, cases):
        self.app.app_steps(cases)

    @pytest.mark.run(order=3)
    @pytest.mark.parametrize('cases', GenericCaseReader(ProjectPaths.ui_security_case, process_ui_row).read())
    def test_android_security_case(self, cases):
        self.app.app_steps(cases)

    @pytest.mark.run(order=4)
    @pytest.mark.parametrize('cases', GenericCaseReader(ProjectPaths.ui_setup_case, process_ui_row).read())
    def test_android_setup_case(self, cases):
        self.app.app_steps(cases)

    @pytest.mark.run(order=5)
    @pytest.mark.parametrize('cases', GenericCaseReader(ProjectPaths.ui_converter_case, process_ui_row).read())
    def test_android_converter_case(self, cases):
        self.app.app_steps(cases)
