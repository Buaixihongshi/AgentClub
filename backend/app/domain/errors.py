class DiscussionError(ValueError):
    """A mission/conversation request the host refuses, with its HTTP status.

    The name predates the extensions split; mission creation and conversation
    lifecycle use it for every refusal, not only discussions.
    """

    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code
