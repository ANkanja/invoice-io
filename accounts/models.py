from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Project user model.

    Defined from the start so fields (e.g. phone, role) can be added later
    without a painful mid-project switch. Staff accounts and permissions use
    Django's built-in groups and model permissions.
    """

    def __str__(self):
        return self.get_full_name() or self.username
