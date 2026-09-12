<template>
  <div
    v-show="false"
    class="comments-wrapper"
    :class="{ transparent: isTransparent }"
  >
    <v-divider class="mb-8" :class="isTransparent ? 'mt-12' : 'mt-8'" />
    <v-sheet class="py-0 title elevation-0" rounded>
      <div class="text-right">
        <v-menu location="bottom" offset-y>
          <template #activator="{ props }">
            <v-btn class="no-uppercase" v-bind="props">
              {{ $t(`Comments.filter_by_${filterThreadsBy}`) }} ({{
                commentTreadsFiltered.length
              }})
              <v-icon location="right" icon="mdi-chevron-down" />
            </v-btn>
          </template>
          <v-list>
            <v-list-item
              v-for="(item, index) in getFilteringOptions()"
              :key="index"
              :value="index"
              @click="filterThreads(item.value)"
            >
              <v-list-item-title
                >{{ $t(`Comments.filter_by_${item.value}`) }} ({{
                  getThreadCountByFilter(item.value)
                }})</v-list-item-title
              >
            </v-list-item>
          </v-list>
        </v-menu>
      </div>

      <v-card-title class="comments-list-title mt-n8">
        {{ $t('Comments.comments_headline') }}
      </v-card-title>
      <v-progress-linear
        v-show="loading"
        indeterminate
        color="primary"
        class="mb-4"
      />
      <v-list class="my-0 py-0">
        <v-list-item
          v-for="thread in commentTreadsFiltered"
          :key="thread.uid"
          class="comment-thread"
        >
          <v-card class="py-0 mb-4" rounded elevation="1" variant="outlined">
            <div
              @mouseover="thread._hovered = true"
              @mouseout="thread._hovered = false"
            >
              <v-row class="mx-5 mt-3 mb-2">
                <v-col cols="8" class="py-1 px-0">
                  {{ thread.author_display_name }}
                  <span class="timestamp text-body-large">
                    {{ $filters.date(thread.created_at) }}
                    {{ isModified(thread.modified_at) }}
                  </span>
                </v-col>

                <v-col cols="4" class="px-0 py-1 text-right comment-actions">
                  <div class="d-inline-block">
                    <div v-show="thread._hovered && isAuthor(thread)">
                      <v-btn
                        size="x-small"
                        :title="$t('Comments.comment_edit')"
                        @click.stop="thread._editMode = true"
                      >
                        <v-icon icon="mdi-pencil-outline" />
                      </v-btn>
                      <v-btn
                        size="x-small"
                        class="ml-4"
                        :title="$t('Comments.comment_delete')"
                        @click="deleteThread(thread.uid)"
                      >
                        <v-icon icon="mdi-delete-outline" />
                      </v-btn>
                    </div>
                  </div>
                  <div class="d-inline-block ml-4">
                    <v-menu location="bottom" offset-y>
                      <template #activator="{ props }">
                        <v-btn
                          :class="
                            thread.status == statuses.COMMENT_STATUS_ACTIVE
                              ? 'orange lighten-2'
                              : 'white'
                          "
                          class="no-uppercase"
                          v-bind="props"
                        >
                          {{ $t(`Comments.comment_status_${thread.status}`) }}
                          <v-icon location="right" icon="mdi-chevron-down" />
                        </v-btn>
                      </template>
                      <v-list>
                        <v-list-item
                          v-for="(item, index) in getActions(thread)"
                          :key="index"
                          :value="index"
                          @click="setThreadStatus(thread.uid, item.newStatus)"
                        >
                          <v-list-item-title>{{
                            $t(`Comments.comment_status_${item.newStatus}`)
                          }}</v-list-item-title>
                        </v-list-item>
                      </v-list>
                    </v-menu>
                  </div>
                </v-col>
              </v-row>

              <v-card-text v-show="!thread._editMode" class="pt-0 pb-3 px-5">
                {{ thread.text }}
              </v-card-text>

              <div v-show="thread._editMode" class="mx-5">
                <v-textarea
                  v-model="thread._newText"
                  auto-grow
                  :label="$t('Comments.comment_edit')"
                />
                <div v-show="!loading" class="mt-n4 pb-4">
                  <v-btn
                    class="secondary-btn"
                    color="white"
                    @click="cancelThreadEdition(thread)"
                  >
                    {{ $t('_global.cancel') }}
                  </v-btn>
                  <v-btn
                    class="primary-btn mx-4"
                    color="secondary"
                    :disabled="thread.text === thread._newText"
                    @click="editThread(thread.uid, thread._newText)"
                  >
                    {{ $t('Comments.comment_edit') }}
                  </v-btn>
                </div>
                <v-progress-linear
                  v-show="loading"
                  indeterminate
                  color="primary"
                  class="mb-10"
                />
              </div>
            </div>

            <!-- Replies -->
            <v-list class="ml-8 mr-0 py-0">
              <v-list-item
                v-for="reply in thread.replies"
                :key="reply.uid"
                class="comment-thread-reply"
                @mouseover="reply._hovered = true"
                @mouseout="reply._hovered = false"
              >
                <v-divider class="mx-5 mt-2" />
                <v-row class="mx-5 mt-3 mb-2">
                  <v-col cols="8" class="py-1 px-0">
                    {{ reply.author_display_name }}
                    <span class="timestamp text-body-large">
                      {{ $filters.date(reply.created_at) }}
                      {{ isModified(reply.modified_at) }}
                    </span>
                  </v-col>
                  <v-col cols="4" class="pa-0 text-right">
                    <div v-show="reply._hovered && isAuthor(reply)">
                      <v-btn
                        size="x-small"
                        :title="$t('Comments.reply_edit')"
                        @click.stop="reply._editMode = true"
                      >
                        <v-icon icon="mdi-pencil-outline" />
                      </v-btn>
                      <v-btn
                        size="x-small"
                        class="ml-4"
                        :title="$t('Comments.reply_delete')"
                        @click.stop="deleteReply(thread.uid, reply.uid)"
                      >
                        <v-icon icon="mdi-delete-outline" />
                      </v-btn>
                    </div>
                  </v-col>
                </v-row>

                <v-card-text v-show="!reply._editMode" class="pt-0 pb-3 px-5">
                  {{ reply.text }}
                </v-card-text>

                <div v-show="reply._editMode" class="mx-5">
                  <v-textarea
                    v-model="reply._newText"
                    auto-grow
                    :label="$t('Comments.reply_edit')"
                  />
                  <div v-show="!loading" class="mt-n4 pb-4">
                    <v-btn
                      class="secondary-btn"
                      color="white"
                      @click="cancelReplyEdition(reply)"
                    >
                      {{ $t('_global.cancel') }}
                    </v-btn>
                    <v-btn
                      class="primary-btn mx-4"
                      color="secondary"
                      :disabled="reply.text === reply._newText"
                      @click="editReply(thread.uid, reply.uid, reply._newText)"
                    >
                      {{ $t('Comments.reply_edit') }}
                    </v-btn>
                  </div>
                  <v-progress-linear
                    v-show="loading"
                    indeterminate
                    color="primary"
                    class="mb-10"
                  />
                </div>
              </v-list-item>
            </v-list>
            <!-- /Replies -->

            <CommentReplyAdd
              :thread-id="thread.uid"
              :thread-status="thread.status"
              :is-transparent="isTransparent"
              @comment-reply-added="getThreads"
            />
          </v-card>
        </v-list-item>
      </v-list>
      <CommentAdd
        :topic-path="topicPath"
        :is-transparent="isTransparent"
        @comment-thread-added="getThreads"
      />
    </v-sheet>
    <ConfirmDialog ref="confirm" :text-cols="6" :action-cols="5" />
  </div>
</template>

<script>
import { computed } from 'vue'
import comments from '@/api/comments'
import CommentAdd from './CommentAdd.vue'
import CommentReplyAdd from './CommentReplyAdd.vue'
import ConfirmDialog from './ConfirmDialog.vue'
import statuses from '@/constants/statuses'
import { useAuthStore } from '@/stores/auth'

export default {
  components: {
    CommentAdd,
    CommentReplyAdd,
    ConfirmDialog,
  },
  props: {
    topicPath: {
      type: String,
      required: true,
    },
    isTransparent: {
      type: Boolean,
      default: true,
    },
  },
  setup() {
    const authStore = useAuthStore()
    return {
      userInfo: computed(() => authStore.userInfo),
    }
  },
  data() {
    return {
      statuses: statuses,
      commentTreads: [],
      commentTreadsFiltered: [],
      filterThreadsBy: 'ALL',
      loading: false,
    }
  },
  watch: {
    topicPath: function () {
      this.getThreads()
    },
  },
  mounted() {
    this.getThreads()
  },
  methods: {
    isModified(modifiedAt) {
      if (modifiedAt) {
        return ' (edited) '
      }
    },
    isAuthor(obj) {
      return this.userInfo.preferred_username.startsWith(obj.author + '@')
    },
    getActions(thread) {
      const actions = []
      if (thread.status === statuses.COMMENT_STATUS_ACTIVE) {
        actions.push({ newStatus: statuses.COMMENT_STATUS_RESOLVED })
      } else {
        actions.push({ newStatus: statuses.COMMENT_STATUS_ACTIVE })
      }
      return actions
    },
    getFilteringOptions() {
      const options = [
        { value: 'ALL' },
        { value: statuses.COMMENT_STATUS_ACTIVE },
        { value: statuses.COMMENT_STATUS_RESOLVED },
      ]
      return options
    },
    filterThreads(filter) {
      this.filterThreadsBy = filter
      switch (filter) {
        case statuses.COMMENT_STATUS_RESOLVED:
          this.commentTreadsFiltered = this.commentTreads.filter(
            (thread) => thread.status === statuses.COMMENT_STATUS_RESOLVED
          )
          break
        case statuses.COMMENT_STATUS_ACTIVE:
          this.commentTreadsFiltered = this.commentTreads.filter(
            (thread) => thread.status === statuses.COMMENT_STATUS_ACTIVE
          )
          break
        default:
          this.commentTreadsFiltered = this.commentTreads
      }
    },
    getThreadCountByFilter(filter) {
      switch (filter) {
        case statuses.COMMENT_STATUS_RESOLVED:
          return this.commentTreads.filter(
            (thread) => thread.status === statuses.COMMENT_STATUS_RESOLVED
          ).length
        case statuses.COMMENT_STATUS_ACTIVE:
          return this.commentTreads.filter(
            (thread) => thread.status === statuses.COMMENT_STATUS_ACTIVE
          ).length
        default:
          return this.commentTreads.length
      }
    },
    async getThreads() {
      if (!this.topicPath) {
        return
      }
      this.loading = true
      comments.getThreads(this.topicPath).then(
        (items) => {
          /*
          _hovered: true indicates that the edit/delete buttons are visible
          _editMode: true indicates that the thread/reply edit form is visible
          _newText: model for the new text of the thread/reply in case of edit
          */
          this.commentTreads = items.map((thread) => {
            thread._newText = thread.text
            thread._hovered = false
            thread._editMode = false
            for (const reply of thread.replies) {
              reply._newText = reply.text
              reply._hovered = false
              reply._editMode = false
            }
            return thread
          })
          this.filterThreads(this.filterThreadsBy)
          this.loading = false
        },
        () => {
          this.loading = false
        }
      )
    },
    async editThread(threadId, newText) {
      this.loading = true

      comments.editThread(threadId, { text: newText }).then(
        () => {
          this.getThreads()
        },
        () => {
          this.loading = false
        }
      )
    },
    async setThreadStatus(threadId, newStatus) {
      this.loading = true

      comments.editThread(threadId, { status: newStatus }).then(
        () => {
          this.getThreads()
        },
        () => {
          this.loading = false
        }
      )
    },
    async editReply(threadId, replyId, newText) {
      this.loading = true
      comments.editReply(threadId, replyId, { text: newText }).then(
        () => {
          this.getThreads()
        },
        () => {
          this.loading = false
        }
      )
    },
    async deleteThread(threadId) {
      const options = {
        type: 'warning',
        cancelLabel: this.$t('_global.cancel'),
        agreeLabel: this.$t('Comments.comment_delete'),
      }
      if (
        await this.$refs.confirm.open(
          this.$t('Comments.comment_delete_approve'),
          options
        )
      ) {
        this.loading = true
        comments.deleteThread(threadId).then(
          () => {
            this.getThreads()
          },
          () => {
            this.loading = false
          }
        )
      }
    },
    async deleteReply(threadId, replyId) {
      const options = {
        type: 'warning',
        cancelLabel: this.$t('_global.cancel'),
        agreeLabel: this.$t('Comments.reply_delete'),
      }
      if (
        await this.$refs.confirm.open(
          this.$t('Comments.reply_delete_approve'),
          options
        )
      ) {
        this.loading = true
        comments.deleteReply(threadId, replyId).then(
          () => {
            this.getThreads()
          },
          () => {
            this.loading = false
          }
        )
      }
    },
    cancelReplyEdition(reply) {
      reply._editMode = false
      reply._newText = reply.text
    },
    cancelThreadEdition(thread) {
      thread._editMode = false
      thread._newText = thread.text
    },
  },
}
</script>

<style scoped>
.comments-wrapper {
  .comment-thread,
  .comment-thread-reply {
    width: 100%;
    display: block;
  }

  .comment-thread-reply.v-list-item {
    padding-left: 0px !important;
    padding-right: 0px !important;
  }

  .timestamp {
    font-weight: 100;
    padding-left: 10px;
  }
}

.comments-wrapper.transparent {
  .comments-list-title {
    padding-left: 0px !important;
    padding-top: 0px !important;
  }
  .v-sheet,
  .v-list,
  .v-list-item {
    background-color: transparent;
    padding-left: 0px !important;
    padding-right: 0px !important;
  }
  .v-list-item {
    background-color: white;
  }
}

.no-uppercase {
  text-transform: unset !important;
}
</style>
