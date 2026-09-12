import repository from './repository'

const resource = 'libraries'

export default {
  get(isEditable) {
    let url = `/${resource}`
    if (isEditable !== undefined) {
      url += `?isEditable=${isEditable}`
    }
    return repository.get(url)
  },
}
