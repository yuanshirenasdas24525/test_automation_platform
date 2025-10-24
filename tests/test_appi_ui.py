
import pytest
from src.core.mobile.start_app import initialize_app, AppManager



class TestUiAutomatic:

    def setup_class(self):
        self.app = AppManager().get_app()

    def test_android_uu_case(self):
        print(self.app)

