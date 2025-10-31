
import pytest
from config.settings import ProjectPaths
from src.core.mobile.start_app import AppManager
from src.utils.read_file import GenericCaseReader, process_ui_row



class TestUiAutomatic:

    def setup_class(self):
        self.app = AppManager().get_app()

    def setup_method(self):
        pass

    @pytest.mark.parametrize('cases', GenericCaseReader(ProjectPaths.ui_register_case, process_ui_row).read())
    def test_android_uu_case(self, cases):
        # print(cases)
        self.app.app_steps(cases)

