<template>
  <NNTable
    :headers="headers"
    item-value="listingRowKey"
    :items-length="total"
    :items="displayItems"
    show-column-names-toggle-button
    :export-data-url="exportDataUrl"
    :export-object-label="exportObjectLabel"
    :column-data-resource="exportDataUrl"
    @filter="fetchData"
  >
    <template
      v-for="header in defaultHeaders"
      #[`item.${header.key}`]="{ item }"
    >
      {{ analysisMetadataValue(item, header.key) }}
    </template>
    <template v-for="(_, slot) of $slots" #[slot]="scope">
      <slot :name="slot" v-bind="scope" />
    </template>
  </NNTable>
</template>

<script>
import { computed } from 'vue'
import filteringParameters from '@/utils/filteringParameters'
import listings from '@/api/listings'
import NNTable from '@/components/tools/NNTable.vue'
import { useStudiesGeneralStore } from '@/stores/studies-general'
import {
  analysisMetadataRowKey,
  analysisMetadataValue,
} from '@/utils/analysisMetadata'

export default {
  components: {
    NNTable,
  },
  props: {
    type: {
      type: String,
      default: '',
    },
    headers: {
      type: Array,
      default: () => [],
    },
  },
  setup() {
    const studiesGeneralStore = useStudiesGeneralStore()
    return {
      selectedStudy: computed(() => studiesGeneralStore.selectedStudy),
      analysisMetadataValue,
    }
  },
  data() {
    return {
      items: [],
      total: 0,
    }
  },
  computed: {
    defaultHeaders() {
      return this.headers.filter((header) => !this.$slots[`item.${header.key}`])
    },
    displayItems() {
      return this.items.map((item) => ({
        ...item,
        listingRowKey: analysisMetadataRowKey(this.type, item),
      }))
    },
    exportDataUrl() {
      return `listings/studies/${this.selectedStudy.uid}/adam/${this.type}`
    },
    exportObjectLabel() {
      return `adam-${this.type}`
    },
  },
  methods: {
    fetchData(filters, options, filtersUpdated) {
      const params = filteringParameters.prepareParameters(
        options,
        filters,
        filtersUpdated
      )
      listings
        .getAllAdam(this.selectedStudy.uid, this.type, params)
        .then((resp) => {
          this.items = resp.data.items
          this.total = resp.data.total
        })
    },
  },
}
</script>
