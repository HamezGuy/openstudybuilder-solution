<template>
  <div class="comment-reply-form mt-4" :class="isTransparent ? 'mx-5' : 'mx-0'">
    <v-textarea
      v-model="text"
      auto-grow
      rows="1"
      :class="isTransparent ? 'mx-0' : 'mx-5'"
      :label="$t('Comments.reply_add_placeholder')"
    />
    <div
      v-show="!loading"
      v-if="text.length > 0"
      class="buttons"
      :class="isTransparent ? 'mx-0' : 'mx-5'"
    >
      <v-btn class="secondary-btn" color="white" @click="cancelCreate">
        {{ $t('_global.cancel') }}
      </v-btn>
      <v-btn
        v-if="threadStatus != statuses.COMMENT_STATUS_ACTIVE"
        class="primary-btn ml-4"
        color="secondary"
        @click="createReply(statuses.COMMENT_STATUS_ACTIVE)"
      >
        {{ $t('Comments.reply_and_reactivate_button') }}
      </v-btn>
      <v-btn
        v-if="threadStatus == statuses.COMMENT_STATUS_ACTIVE"
        class="primary-btn ml-4"
        color="secondary"
        @click="createReply(statuses.COMMENT_STATUS_RESOLVED)"
      >
        {{ $t('Comments.reply_and_resolve_button') }}
      </v-btn>
      <v-btn
        class="primary-btn ml-4"
        color="secondary"
        @click="createReply(null)"
      >
        {{ $t('Comments.reply_add_button') }}
      </v-btn>
    </div>
    <v-progress-linear
      v-show="loading"
      indeterminate
      color="primary"
      class="mb-10"
    />
  </div>
</template>

<script>
import comments from '@/api/comments'
import statuses from '@/constants/statuses'

export default {
  props: {
    threadId: {
      type: String,
      required: true,
    },
    threadStatus: {
      type: String,
      required: true,
    },
    isTransparent: {
      type: Boolean,
      default: false,
    },
  },
  emits: ['commentReplyAdded'],
  data() {
    return {
      statuses: statuses,
      text: '',
      loading: false,
    }
  },
  methods: {
    createReply(newThreadStatus) {
      this.loading = true
      comments.createReply(this.threadId, this.text, newThreadStatus).then(
        () => {
          this.text = ''
          this.loading = false
          this.$emit('commentReplyAdded')
        },
        () => {
          this.loading = false
        }
      )
    },
    cancelCreate() {
      this.text = ''
    },
  },
}
</script>

<style scoped>
.comment-reply-form {
  margin-bottom: -1rem;
}
.buttons {
  margin-top: -1rem;
  padding-bottom: 2rem;
}
</style>
