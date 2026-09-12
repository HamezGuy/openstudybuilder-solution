<template>
  <div>
    <v-tooltip v-if="props.sortMode && (!props.isParentDraft || !hasSiblings)">
      <template #activator="{ props }">
        <div v-bind="props" :style="{ cursor: 'not-allowed' }">
          <v-btn
            size="small"
            variant="text"
            icon="mdi-arrow-up-thin"
            :disabled="true"
          />
          <v-btn
            size="small"
            variant="text"
            icon="mdi-arrow-down-thin"
            :disabled="true"
          />
        </div>
      </template>

      <span v-html="t('CRFTree.reordering_not_allowed')"></span>
    </v-tooltip>
    <div v-else-if="props.sortMode">
      <v-btn
        size="small"
        variant="text"
        icon="mdi-arrow-up-thin"
        :disabled="index === 0"
        @click="orderUp(item, index)"
      />
      <v-btn
        size="small"
        variant="text"
        icon="mdi-arrow-down-thin"
        :disabled="isLast"
        @click="orderDown(item, index)"
      />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const props = defineProps({
  sortMode: { type: Boolean, required: true },
  isParentDraft: { type: Boolean, required: true },
  siblingLength: { type: Object, required: true },
  item: { type: Object, required: true },
  index: { type: Number, required: true },
})

const hasSiblings = computed(() => props.siblingLength > 1)
const isLast = computed(() => props.siblingLength - 1 === props.index)

const emit = defineEmits(['orderUp', 'orderDown'])

const orderUp = (item, index) => emit('orderUp', item, index)
const orderDown = (item, index) => emit('orderDown', item, index)
</script>
