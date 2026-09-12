<template>
  <v-row>
    <v-col cols="6">
      <v-autocomplete
        v-model="value"
        :label="$t('SubstanceField.label')"
        :items="substances"
        item-title="name"
        return-object
      />
    </v-col>
    <v-col cols="6">
      <v-text-field :value="unii" label="UNII" disabled hide-details />
    </v-col>
    <v-col cols="6">
      <v-text-field
        :value="pclass"
        :label="$t('SubstanceField.pharmacological_class')"
        disabled
        hide-details
      />
    </v-col>
    <v-col cols="6">
      <v-text-field
        :value="medrtId"
        :label="$t('SubstanceField.medrt')"
        disabled
        hide-details
      />
    </v-col>
  </v-row>
</template>

<script>
import { computed } from 'vue'
import { useCompoundsStore } from '@/stores/library-compounds'

export default {
  props: {
    modelValue: {
      type: Object,
      default: null,
    },
  },
  emits: ['update:modelValue'],
  setup() {
    const compoundsStore = useCompoundsStore()
    return {
      substances: computed(() => compoundsStore.substances),
    }
  },
  data() {
    return {
      medrtId: '',
      pclass: '',
      unii: '',
    }
  },
  computed: {
    value: {
      get() {
        return this.modelValue
      },
      set(value) {
        this.fillInformation(value)
        this.$emit('update:modelValue', value)
      },
    },
  },
  watch: {
    substances(value) {
      if (value) {
        this.fillInformation(this.value)
      }
    },
  },
  mounted() {
    if (this.modelValue) {
      this.fillInformation(this.value)
    }
  },
  methods: {
    fillInformation(value) {
      if (value) {
        if (value.pclass) {
          this.pclass = value.pclass.name
          this.medrtId = value.pclass.dictionary_id
        } else {
          this.pclass = ''
          this.medrtId = ''
        }
        this.unii = value.dictionary_id
      }
    },
  },
}
</script>
