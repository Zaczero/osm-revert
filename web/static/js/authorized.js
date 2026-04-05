const modal = document.getElementById('first-time-modal')
if (!localStorage.getItem('first-time-modal-acknowledged')) {
	console.log('Showing first-time-modal')
	const backdrop = document.createElement('div')
	backdrop.classList.add('modal-backdrop', 'show')
	document.body.appendChild(backdrop)

	modal.classList.add('d-block')
	const acceptButton = modal.querySelector('button')
	acceptButton.addEventListener(
		'click',
		() => {
			console.debug('Acknowledging first-time-modal')
			localStorage.setItem('first-time-modal-acknowledged', 'true')
			backdrop.remove()
			modal.remove()
		},
		{ once: true },
	)
	acceptButton.focus()
} else {
	console.log('Not showing first-time-modal')
	modal.remove()
}

const changesets = document.getElementById('changesets')
const overpass_url = document.getElementById('overpass-url')
const overpass_url_reset = document.getElementById('overpass-url-reset')
const query_filter = document.getElementById('query-filter')
const comment = document.getElementById('comment')
const discussion = document.getElementById('discussion')
const submit = document.getElementById('submit')
const submit_osc = document.getElementById('submit-osc')
const log = document.getElementById('log')
const ws = new WebSocket(
	`${document.location.protocol === 'https:' ? 'wss' : 'ws'}://${document.location.host}/ws`,
)
let isAutoScrolling = true

const appendLogMessage = (message) => {
	log.value += message + '\n'
	if (isAutoScrolling && log.scrollHeight > log.clientHeight)
		log.scrollTop = log.scrollHeight
}

const compressData = async (text) => {
	const stream = new Blob([text]).stream().pipeThrough(new CompressionStream('deflate'))
	const compressed = await new Response(stream).arrayBuffer()
	return btoa(String.fromCharCode(...new Uint8Array(compressed)))
}

const activeRequests = new Map()
const OVERPASS_MAX_RETRIES = 6
const OVERPASS_RETRY_INTERVAL_MS = 10000

const waitWithAbort = (ms, signal) =>
	new Promise((resolve, reject) => {
		const timeout = setTimeout(() => {
			signal.removeEventListener('abort', onAbort)
			resolve()
		}, ms)

		const onAbort = () => {
			clearTimeout(timeout)
			reject(new DOMException('Aborted', 'AbortError'))
		}

		if (signal.aborted) {
			onAbort()
			return
		}

		signal.addEventListener('abort', onAbort, { once: true })
	})

const fetchOverpassWithRetries = async (id, url, query, signal) => {
	for (let attempt = 0; attempt <= OVERPASS_MAX_RETRIES; attempt++) {
		const response = await fetch(url, {
			method: 'POST',
			body: new URLSearchParams({ data: query }),
			headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
			credentials: 'omit',
			priority: 'high',
			signal: signal,
		})

		if (response.status !== 429 && response.status !== 504 || attempt === OVERPASS_MAX_RETRIES) {
			return response
		}

		appendLogMessage(
			`⚠️ Overpass returned ${response.status} status code, retrying in ${OVERPASS_RETRY_INTERVAL_MS / 1000}s (${attempt + 1}/${OVERPASS_MAX_RETRIES})`,
		)
		await waitWithAbort(OVERPASS_RETRY_INTERVAL_MS, signal)
	}
}

const processOverpassRequest = async (id, url, query) => {
	console.info(`[${id}] Processing overpass request`)
	const controller = new AbortController()
	activeRequests.set(id, controller)

	try {
		const response = await fetchOverpassWithRetries(id, url, query, controller.signal)

		const data = await response.text()
		const compressed = await compressData(data)
		console.info(`[${id}] Uploading overpass response:`, response.status)
		ws.send(
			JSON.stringify({
				type: 'overpass_response',
				id: id,
				status: response.status,
				data: compressed,
			}),
		)
	} catch (error) {
		console.error(`[${id}]`, error)
		ws.send(
			JSON.stringify({
				type: 'overpass_response',
				id: id,
				status: 0,
				error: error.toString(),
			}),
		)
	} finally {
		activeRequests.delete(id)
	}
}

// Load saved Overpass URL from localStorage or use default
const savedOverpassUrl = localStorage.getItem('overpass-url')
if (savedOverpassUrl) overpass_url.value = savedOverpassUrl

// Save Overpass URL to localStorage when it changes
overpass_url.addEventListener('change', () => {
	localStorage.setItem('overpass-url', overpass_url.value)
})

// Reset Overpass URL to default
overpass_url_reset.addEventListener('click', () => {
	overpass_url.value = overpass_url.placeholder
	localStorage.removeItem('overpass-url')
})

let isReverting = true
let clearFields = false

let wsDownloadingOsc = false
let wsOsc = []

const setIsReverting = (state) => {
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

ws.onmessage = (e) => {
	const obj = JSON.parse(e.data)

	// Handle proxy requests
	if (obj.type === 'overpass_request') {
		processOverpassRequest(obj.id, obj.url, obj.query)
		return
	}

	if (obj.message === '<osc>') {
		wsDownloadingOsc = true
		wsOsc = []
	} else if (obj.message === '</osc>') {
		const fileName = 'revert_' + new Date().toISOString().replace(/:/g, '_') + '.osc'
		const osc = wsOsc.join('\n')

		const a = document.createElement('a')
		const file = new Blob([osc], { type: 'text/xml; charset=utf-8' })
		a.href = URL.createObjectURL(file)
		a.download = fileName
		a.click()

		wsDownloadingOsc = false
		wsOsc = []
	} else if (wsDownloadingOsc) {
		wsOsc.push(obj.message)
	} else {
		appendLogMessage(obj.message)
	}

	if (obj.last === true) {
		if (clearFields && obj.message === 'Exit code: 0') {
			changesets.value = ''
		}

		setIsReverting(false)
	}
}

ws.onclose = (e) => {
	console.log(e)
	setIsReverting(true)
	submit.value = 'Disconnected'
	submit_osc.value = 'Disconnected'
	log.value = `⚠️ Disconnected: ${e.reason}\n⚠️ Please reload the page`

	// Cancel all active proxy requests
	for (const controller of activeRequests.values()) controller.abort()
	activeRequests.clear()
}

const beginRevert = (upload) => {
	if (isReverting) return

	setIsReverting(true)
	clearFields = upload
	log.value = ''

	ws.send(
		JSON.stringify({
			changesets: changesets.value,
			overpass_url: overpass_url.value || overpass_url.placeholder,
			query_filter: query_filter.value,
			comment: comment.value,
			upload: upload,
			discussion: discussion.value,
			discussion_target: document.querySelector(
				'input[name="discussion_target"]:checked',
			).value,
			fix_parents:
				document.querySelector('input[name="fix_parents"]:checked').value === 'True',
		}),
	)
}

submit.addEventListener('click', (e) => {
	e.preventDefault()
	beginRevert(true)
})

submit_osc.addEventListener('click', (e) => {
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
			if (charsLeft <= 0) counter.textContent = 'No characters left'
			else
				counter.textContent = `${charsLeft} character${charsLeft !== 1 ? 's' : ''} left`

			counter.style.color = charsLeft <= 20 ? 'red' : 'initial'
			counter.style.display = 'block'
		} else {
			counter.style.display = 'none'
		}
	}
}
