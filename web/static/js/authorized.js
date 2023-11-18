const form = document.getElementById('form')
const changesets = document.getElementById('changesets')
const query_filter = document.getElementById('query-filter')
const comment = document.getElementById('comment')
const discussion = document.getElementById('discussion')
const submit = document.getElementById('submit')
const submit_osc = document.getElementById('submit-osc')
const log = document.getElementById('log')
const ws = new WebSocket(`${document.location.protocol === 'https:' ? 'wss' : 'ws'}://${document.location.host}/ws`)

let isAutoScrolling = true
let isReverting = true
let clearFields = false

let wsDownloadingOsc = false
let wsOsc = []

const setIsReverting = state => {
    if (state) {
        wsDownloadingOsc = false
        wsOsc = []
    }

    isReverting = state
    submit.disabled = state
    submit_osc.disabled = state
}

ws.onopen = () => {
    submit.value = 'Revert and upload'
    submit_osc.value = '💾 Revert and download .osc'
    setIsReverting(false)
}

ws.onmessage = e => {
    const obj = JSON.parse(e.data)

    if (obj.message === "<osc>") {
        wsDownloadingOsc = true
        wsOsc = []
    }
    else if (obj.message === "</osc>") {
        const fileName = 'revert_' + new Date().toISOString().replace(/:/g, '_') + '.osc'
        const osc = wsOsc.join('\n')

        const a = document.createElement('a')
        const file = new Blob([osc], { type: 'text/xml; charset=utf-8' })
        a.href = URL.createObjectURL(file)
        a.download = fileName
        a.click()

        wsDownloadingOsc = false
        wsOsc = []
    }
    else if (wsDownloadingOsc) {
        wsOsc.push(obj.message)
    }
    else {
        log.value += obj.message + '\n'

        if (isAutoScrolling && log.scrollHeight > log.clientHeight)
            log.scrollTop = log.scrollHeight
    }

    if (obj.last === true) {
        if (clearFields && obj.message === "Exit code: 0") {
            changesets.value = ''
        }

        setIsReverting(false)
    }
}

ws.onclose = e => {
    console.log(e)
    setIsReverting(true)
    submit.value = 'Disconnected'
    submit_osc.value = 'Disconnected'
    log.value = `⚠️ Disconnected: ${e.reason}\n⚠️ Please reload the page`
}

const beginRevert = upload => {
    if (isReverting)
        return

    setIsReverting(true)
    clearFields = upload
    log.value = ''

    ws.send(JSON.stringify({
        changesets: changesets.value,
        query_filter: query_filter.value,
        comment: comment.value,
        upload: upload,
        discussion: discussion.value,
        discussion_target: document.querySelector('input[name="discussion_target"]:checked').value,
        fix_parents: document.querySelector('input[name="fix_parents"]:checked').value === 'True',
    }))
}

submit.addEventListener('click', e => {
    e.preventDefault()

    beginRevert(true)
})

submit_osc.addEventListener('click', e => {
    e.preventDefault()

    beginRevert(false)
})

log.addEventListener('scroll', () => {
    isAutoScrolling = log.scrollHeight - log.scrollTop < log.clientHeight + 5
})

for (const counter of document.querySelectorAll('.char-counter')) {
    const input = document.getElementById(counter.getAttribute('for'))
    const maxLength = input.getAttribute('maxlength')

    input.oninput = () => {
        const charsLeft = maxLength - [...input.value].length
        if (charsLeft <= 100) {
            if (charsLeft <= 0)
                counter.textContent = `No characters left`
            else
                counter.textContent = `${charsLeft} character${charsLeft !== 1 ? 's' : ''} left`

            counter.style.color = charsLeft <= 20 ? 'red' : 'initial'
            counter.style.display = 'block'
        } else {
            counter.style.display = 'none'
        }
    }
}
