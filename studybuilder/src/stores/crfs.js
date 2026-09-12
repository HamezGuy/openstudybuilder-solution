import { defineStore } from 'pinia'
import crfs from '@/api/crfs'

export const useCrfsStore = defineStore('crfs', {
  state: () => ({
    collections: [],
    totalCollections: 0,
    forms: [],
    totalForms: 0,
    itemGroups: [],
    totalItemGroups: 0,
    items: [],
    totalItems: 0,
  }),

  actions: {
    fetchCollections(params) {
      if (!params) {
        params.total_count = true
      }
      return crfs.get('study-events', { params }).then((resp) => {
        this.collections = resp.data.items
        this.totalCollections = resp.data.total
      })
    },
    fetchForms(params) {
      return crfs.get('forms', { params }).then((resp) => {
        this.forms = resp.data.items
        this.totalForms = resp.data.total
      })
    },
    fetchItemGroups(params) {
      return crfs.get('item-groups', { params }).then((resp) => {
        this.itemGroups = resp.data.items
        this.totalItemGroups = resp.data.total
      })
    },
    fetchItems(params) {
      return crfs.get('items', { params }).then((resp) => {
        this.items = resp.data.items
        this.totalItems = resp.data.total
      })
    },
  },
})
