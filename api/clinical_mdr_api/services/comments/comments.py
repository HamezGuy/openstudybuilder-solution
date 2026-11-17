import copy
import re
from datetime import datetime

from neomodel import db  # type: ignore

from clinical_mdr_api.domains.comments.comments import (
    CommentReplyAR,
    CommentThreadAR,
    CommentThreadStatus,
    CommentTopicAR,
)
from clinical_mdr_api.models.comments.comments import (
    CommentReply,
    CommentReplyCreateInput,
    CommentReplyEditInput,
    CommentThread,
    CommentThreadCreateInput,
    CommentThreadEditInput,
    CommentTopic,
)
from clinical_mdr_api.models.utils import GenericFilteringReturn
from clinical_mdr_api.services._meta_repository import MetaRepository  # type: ignore
from clinical_mdr_api.services.studies.study_visibility import (
    assert_study_authority,
    assert_tied_to_study,
    delegated_study_scope_required,
    study_readable,
    visible_study_scope,
)
from common.auth.user import user
from common.exceptions import ForbiddenException, NotFoundException

# The study a comment topic names, and the study activity a study-activities
# topic names (D-45, OSB_ISOLATION_DESIGN.md section 5.3).
_TOPIC_STUDY = re.compile(r"^/?studies/(?P<uid>[^/]+)(?:/|$)")
_TOPIC_STUDY_ACTIVITY = re.compile(
    r"^/?studies/(?P<uid>[^/]+)/study-activities/(?P<child>[^/]+)(?:/|$)"
)


def _assert_topic_study_readable(topic_path: str | None) -> str:
    """Under the delegated profile, the readable study a topic names.

    A topic that names no study is refused with 403, and names nothing. A study
    the caller cannot read answers the 404 of an unknown study, naming the uid
    the caller typed, whether that study exists elsewhere or not.
    """
    match = _TOPIC_STUDY.match(topic_path or "")
    if match is None:
        raise ForbiddenException(
            msg="A comment topic must name a study in delegated mode."
        )
    study_uid = match.group("uid")
    NotFoundException.raise_if_not(
        study_readable(study_uid), "Study Definition", study_uid
    )
    return study_uid


def _strict_listing_arguments(topic_path: str | None) -> dict | None:
    """The extra repository arguments of a delegated listing, after its checks.

    None outside the delegated profile, so the repository receives today's
    arguments. With a topic, its study must be readable and the match is
    case-sensitive. Without one, the visible studies become topic prefixes,
    and an empty scope gives an empty prefix list, which the listing answers
    with an empty page and no query.
    """
    if not delegated_study_scope_required():
        return None
    if topic_path is not None:
        _assert_topic_study_readable(topic_path)
        return {"exact_case": True}
    prefixes = [
        prefix
        for uid in visible_study_scope() or ()
        for prefix in (f"studies/{uid}", f"/studies/{uid}")
    ]
    return {"exact_case": True, "topic_prefixes": prefixes}


class CommentsService:
    def __init__(self):
        self.user_info = user()
        self.repos = MetaRepository()

    def get_all_comment_topics(
        self,
        topic_path: str | None = None,
        topic_path_partial_match: bool = False,
        page_number: int = 1,
        page_size: int = 0,
    ) -> GenericFilteringReturn[CommentTopic]:
        assert_study_authority(require_write=False)
        strict_arguments = _strict_listing_arguments(topic_path)
        if (
            strict_arguments is not None
            and strict_arguments.get("topic_prefixes") == []
        ):
            return GenericFilteringReturn(items=[], total=0)
        try:
            item_ars, total = self.repos.comments_repository.find_all_comment_topics(
                topic_path=topic_path,
                topic_path_partial_match=topic_path_partial_match,
                page_number=page_number,
                page_size=page_size,
                **(strict_arguments or {}),
            )

            items = [CommentTopic.from_ar(item_ar) for item_ar in item_ars]
            return GenericFilteringReturn(items=items, total=total)
        finally:
            self.repos.close()

    def get_all_comment_threads(
        self,
        topic_path: str | None = None,
        topic_path_partial_match: bool = False,
        status: CommentThreadStatus | None = None,
        page_number: int = 1,
        page_size: int = 0,
    ) -> GenericFilteringReturn[CommentThread]:
        assert_study_authority(require_write=False)
        strict_arguments = _strict_listing_arguments(topic_path)
        if (
            strict_arguments is not None
            and strict_arguments.get("topic_prefixes") == []
        ):
            return GenericFilteringReturn(items=[], total=0)
        try:
            item_ars, total = self.repos.comments_repository.find_all_comment_threads(
                topic_path=topic_path,
                topic_path_partial_match=topic_path_partial_match,
                status=status,
                page_number=page_number,
                page_size=page_size,
                **(strict_arguments or {}),
            )

            items = [CommentThread.from_ar(item_ar) for item_ar in item_ars]
            return GenericFilteringReturn(items=items, total=total)
        finally:
            self.repos.close()

    def get_comment_thread(self, uid: str) -> CommentThread:
        assert_study_authority(require_write=False)
        repos = MetaRepository()
        try:
            comment_thread = CommentThread.from_uid(
                uid, repos.comments_repository.find_comment_thread_by_uid
            )
            NotFoundException.raise_if(comment_thread is None, "Comment Thread", uid)
            comment_thread.replies = [
                CommentReply.from_ar(reply_ar)
                for reply_ar in self.repos.comments_repository.find_all_comment_thread_replies(
                    uid
                )
            ]
            return comment_thread
        finally:
            self.repos.close()

    @db.transaction
    def create_comment_thread(
        self, comment_thread_create_input: CommentThreadCreateInput
    ) -> CommentThread:
        assert_study_authority(require_write=False)
        if delegated_study_scope_required():
            # The topic's study, and a study activity it names, before any uid
            # is generated or anything is read or saved (D-45, section 5.3).
            study_uid = _assert_topic_study_readable(
                comment_thread_create_input.topic_path
            )
            activity = _TOPIC_STUDY_ACTIVITY.match(
                comment_thread_create_input.topic_path
            )
            if activity is not None:
                assert_tied_to_study(
                    study_uid, children={"Study Activity": activity.group("child")}
                )
        try:
            comment_thread_ar = CommentThreadAR.from_input_values(
                text=comment_thread_create_input.text,
                topic_path=comment_thread_create_input.topic_path,
                author_id=self.user_info.id(),
                author_display_name=self.user_info.name,
                status=CommentThreadStatus.ACTIVE,
                created_at=datetime.now(),
                generate_uid_callback=self.repos.comments_repository.generate_thread_uid,
            )

            # Retrieve existing topic or create one if not found
            comment_topic_ar = self.repos.comments_repository.find_topic_by_path(
                comment_thread_create_input.topic_path
            )
            if not comment_topic_ar:
                comment_topic_ar = CommentTopicAR.from_input_values(
                    topic_path=comment_thread_create_input.topic_path,
                    generate_uid_callback=self.repos.comments_repository.generate_topic_uid,
                )
                self.repos.comments_repository.save_comment_topic(comment_topic_ar)

            self.repos.comments_repository.save_comment_thread(comment_thread_ar)
            return CommentThread.from_uid(
                comment_thread_ar.uid,
                self.repos.comments_repository.find_comment_thread_by_uid,
            )
        finally:
            self.repos.close()

    def _edit_comment_thread(
        self, uid: str, comment_thread_edit_input: CommentThreadEditInput
    ) -> CommentThread:
        assert_study_authority(require_write=False)
        try:
            comment_thread_latest = (
                self.repos.comments_repository.find_comment_thread_by_uid(uid)
            )
            NotFoundException.raise_if(
                comment_thread_latest is None, "Comment Thread", uid
            )

            comment_thread_previous = copy.deepcopy(comment_thread_latest)

            if comment_thread_edit_input.text is not None:
                ForbiddenException.raise_if(
                    comment_thread_latest.author_id != self.user_info.id(),
                    msg="Only the author can edit a comment thread.",
                )
                comment_thread_latest.text = comment_thread_edit_input.text

            if comment_thread_edit_input.status is not None:
                comment_thread_latest.status = comment_thread_edit_input.status

            if comment_thread_previous != comment_thread_latest:
                self.repos.comments_repository.edit_comment_thread(
                    comment_thread_latest,
                    comment_thread_previous,
                    self.user_info.id(),
                )

            return CommentThread.from_uid(
                comment_thread_latest.uid,
                self.repos.comments_repository.find_comment_thread_by_uid,
            )
        finally:
            self.repos.close()

    @db.transaction
    def edit_comment_thread(
        self, uid: str, comment_thread_edit_input: CommentThreadEditInput
    ) -> CommentThread:
        return self._edit_comment_thread(uid, comment_thread_edit_input)

    @db.transaction
    def delete_comment_thread(self, uid: str):
        assert_study_authority(require_write=False)
        comment_thread = self.repos.comments_repository.find_comment_thread_by_uid(uid)
        ForbiddenException.raise_if(
            comment_thread and comment_thread.author_id != self.user_info.id(),
            msg="Only the author can delete a comment thread.",
        )
        self.repos.comments_repository.delete_comment_thread(uid)

    def get_all_comment_thread_replies(self, thread_uid: str) -> list[CommentReply]:
        assert_study_authority(require_write=False)
        try:
            all_items = self.repos.comments_repository.find_all_comment_thread_replies(
                thread_uid
            )
            self.repos.comments_repository.close()
            return [CommentReply.from_ar(item_ar) for item_ar in all_items]
        finally:
            self.repos.close()

    def get_comment_thread_reply(self, uid: str) -> CommentReply:
        assert_study_authority(require_write=False)
        repos = MetaRepository()
        try:
            item = CommentReply.from_uid(
                uid, repos.comments_repository.find_comment_reply_by_uid
            )
            NotFoundException.raise_if(item is None, "Comment Reply", uid)
            return item
        finally:
            self.repos.close()

    @db.transaction
    def create_comment_reply(
        self, thread_uid: str, create_input: CommentReplyCreateInput
    ) -> CommentReply:
        assert_study_authority(require_write=False)
        try:
            comment_reply_ar = CommentReplyAR.from_input_values(
                text=create_input.text,
                comment_thread_uid=thread_uid,
                author_id=self.user_info.id(),
                author_display_name=self.user_info.name,
                created_at=datetime.now(),
                generate_uid_callback=self.repos.comments_repository.generate_reply_uid,
            )

            # Validate that the comment thread exists
            NotFoundException.raise_if_not(
                self.repos.comments_repository.find_comment_thread_by_uid(thread_uid),
                "Comment Thread",
                thread_uid,
            )

            if create_input.thread_status:
                self._edit_comment_thread(
                    thread_uid,
                    CommentThreadEditInput(
                        status=create_input.thread_status,
                    ),
                )

            self.repos.comments_repository.save_comment_reply(comment_reply_ar)
            return CommentReply.from_uid(
                comment_reply_ar.uid,
                self.repos.comments_repository.find_comment_reply_by_uid,
            )
        finally:
            self.repos.close()

    @db.transaction
    def edit_comment_thread_reply(
        self, uid: str, reply_edit_input: CommentReplyEditInput
    ) -> CommentReply:
        assert_study_authority(require_write=False)
        try:
            reply_latest = self.repos.comments_repository.find_comment_reply_by_uid(uid)

            NotFoundException.raise_if(
                reply_latest is None, "Comment Thread Reply", uid
            )

            reply_previous = copy.deepcopy(reply_latest)

            if reply_edit_input.text != reply_latest.text:
                ForbiddenException.raise_if(
                    reply_latest.author_id != self.user_info.id(),
                    msg="Only the author can edit a comment thread reply.",
                )
                reply_latest.text = reply_edit_input.text

                self.repos.comments_repository.edit_comment_reply(
                    reply_latest, reply_previous
                )

            return CommentReply.from_uid(
                reply_latest.uid,
                self.repos.comments_repository.find_comment_reply_by_uid,
            )
        finally:
            self.repos.close()

    @db.transaction
    def delete_comment_reply(self, uid: str):
        assert_study_authority(require_write=False)
        comment_reply = self.repos.comments_repository.find_comment_reply_by_uid(uid)
        ForbiddenException.raise_if(
            comment_reply and comment_reply.author_id != self.user_info.id(),
            msg="Only the author can delete a comment reply.",
        )
        self.repos.comments_repository.delete_comment_reply(uid)
