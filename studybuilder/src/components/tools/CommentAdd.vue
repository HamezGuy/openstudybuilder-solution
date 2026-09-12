<template>
  <div>
    <v-textarea
      v-model="text"
      auto-grow
      rows="1"
      :class="isTransparent ? 'mx-0' : 'mx-5'"
      :label="$t('Comments.comment_add_placeholder')"
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
      <v-btn class="primary-btn mx-4" color="secondary" @click="createThread">
        {{ $t('Comments.comment_add_button') }}
      </v-btn>
    </div>
    <v-progress-linear
      v-show="loading"
      indeterminate
      color="primary"
      class="mb-4"
    />
  </div>
</template>

<script>
import comments from '@/api/comments'

export default {
  props: {
    topicPath: {
      type: String,
      required: true,
    },
    isTransparent: {
      type: Boolean,
      default: false,
    },
  },
  emits: ['commentThreadAdded'],
  data() {
    return {
      text: '',
      loading: false,
    }
  },
  methods: {
    createThread() {
      const data = {
        topic_path: this.topicPath,
        text: this.text,
      }
      this.loading = true
      comments.createThread(data).then(
        () => {
          this.text = ''
          this.loading = false
          this.$emit('commentThreadAdded')
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
.buttons {
  margin-top: -1rem;
  padding-bottom: 2rem;
}
</style>
