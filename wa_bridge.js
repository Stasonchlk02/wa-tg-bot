import makeWASocket, {
    useMultiFileAuthState,
    DisconnectReason,
    makeCacheableSignalKeyStore,
    fetchLatestBaileysVersion
} from '@whiskeysockets/baileys'
import express from 'express'
import pino from 'pino'
import fs from 'fs'

const logger = pino({ level: 'silent' })
const app = express()
app.use(express.json())

const AUTH_DIR = process.env.AUTH_DIR || '/tmp/wa_auth_info'
const PORT = process.env.WA_PORT || process.env.PORT || 3001
const PHONE_NUMBER = process.env.WA_PHONE || ''

let sock = null
let isConnected = false
let connectionStatus = 'disconnected'
let pairingCode = null
let reconnectAttempts = 0
const MAX_RECONNECT = 5

function normalizePhone(phone) {
    if (!phone) return ''
    // Оставляем только цифры
    return phone.replace(/[^0-9]/g, '')
}

function ensureAuthDir() {
    if (!fs.existsSync(AUTH_DIR)) {
        fs.mkdirSync(AUTH_DIR, { recursive: true })
        console.log(`✅ Создана папка ${AUTH_DIR}`)
    }
}

function setupConnectionHandlers(socket, isMainConnection = false) {
    socket.ev.on('connection.update', async (update) => {
        console.log('🔔 connection.update:', JSON.stringify({
            connection: update.connection,
            statusCode: update.lastDisconnect?.error?.output?.statusCode,
            hasPairingCode: !!update.pairingCode,
        }))

        const { connection, lastDisconnect, pairingCode: pc } = update

        if (pc) {
            pairingCode = pc
            connectionStatus = 'waiting_pair'
            console.log(`\n${'='.repeat(40)}`)
            console.log(`🔐 PAIRING CODE: ${pc}`)
            console.log(`${'='.repeat(40)}\n`)
        }

        if (connection === 'open') {
            isConnected = true
            connectionStatus = 'connected'
            reconnectAttempts = 0
            pairingCode = null
            console.log('✅ WhatsApp подключён!')
            console.log(`👤 ID: ${socket?.user?.id}`)
        }

        if (connection === 'close') {
            isConnected = false
            connectionStatus = 'disconnected'
            const statusCode = lastDisconnect?.error?.output?.statusCode
            const reason = lastDisconnect?.error?.message || 'unknown'
            console.log(`❌ Соединение закрыто. Код: ${statusCode}, причина: ${reason}`)

            if (statusCode === DisconnectReason.loggedOut) {
                console.log('🚪 Разлогинен. Удаляю сессию...')
                try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }) } catch {}
                ensureAuthDir()
                connectionStatus = 'logged_out'
                // Не переподключаемся автоматически
            } else if (isMainConnection && reconnectAttempts < MAX_RECONNECT) {
                reconnectAttempts++
                const delay = Math.min(reconnectAttempts * 5000, 30000)
                console.log(`🔄 Переподключение через ${delay/1000}с (${reconnectAttempts}/${MAX_RECONNECT})...`)
                setTimeout(() => connectToWhatsApp(), delay)
            } else if (!isMainConnection) {
                // После pairing сокет перешёл в главный режим
                setTimeout(() => connectToWhatsApp(), 3000)
            }
        }
    })
}

async function connectToWhatsApp() {
    ensureAuthDir()

    try {
        const { version } = await fetchLatestBaileysVersion()
        console.log(`📱 WA версия: ${version}`)

        const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)

        sock = makeWASocket({
            version,
            auth: {
                creds: state.creds,
                keys: makeCacheableSignalKeyStore(state.keys, logger),
            },
            printQRInTerminal: false,
            logger,
            browser: ['WA Bridge', 'Chrome', '120.0.0'],
            defaultQueryTimeoutMs: 15000,
            generateHighQualityLinkPreview: false,
            syncFullHistory: false,
            markOnlineOnConnect: false,
            connectTimeoutMs: 30000,
            keepAliveIntervalMs: 25000,
        })

        setupConnectionHandlers(sock, true)
        sock.ev.on('creds.update', saveCreds)

    } catch (error) {
        console.error('❌ Ошибка connectToWhatsApp:', error.message)
        if (reconnectAttempts < MAX_RECONNECT) {
            reconnectAttempts++
            setTimeout(() => connectToWhatsApp(), 10000)
        }
    }
}

// === HTTP API ===

app.get('/health', (req, res) => {
    res.json({ status: 'ok', connected: isConnected, connectionStatus })
})

app.get('/status', (req, res) => {
    res.json({
        connected: isConnected,
        connectionStatus,
        phone: sock?.user?.id ? sock.user.id.split(':')[0] : null,
        pairingCode: pairingCode || null,
    })
})

app.post('/pair', async (req, res) => {
    const { phone } = req.body
    const targetPhone = phone || PHONE_NUMBER

    if (!targetPhone) {
        return res.status(400).json({
            error: 'Номер телефона не указан. Передайте phone или установите WA_PHONE.'
        })
    }

    if (isConnected) {
        return res.json({ success: true, message: 'Уже подключён', alreadyConnected: true })
    }

    try {
        // Закрываем старый сокет
        if (sock) {
            try { sock.end(undefined) } catch {}
            sock = null
            isConnected = false
            await new Promise(resolve => setTimeout(resolve, 2000))
        }

        ensureAuthDir()
        const { version } = await fetchLatestBaileysVersion()
        const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)

        const newSock = makeWASocket({
            version,
            auth: {
                creds: state.creds,
                keys: makeCacheableSignalKeyStore(state.keys, logger),
            },
            printQRInTerminal: false,
            logger,
            browser: ['WA Bridge', 'Chrome', '120.0.0'],
            defaultQueryTimeoutMs: 15000,
            generateHighQualityLinkPreview: false,
            syncFullHistory: false,
            markOnlineOnConnect: false,
        })

        newSock.ev.on('creds.update', saveCreds)

        // Ждём инициализации
        await new Promise(resolve => setTimeout(resolve, 3000))

        // Только цифры без +
        const cleanPhone = normalizePhone(targetPhone)
        console.log(`📲 Запрашиваю pairing code для +${cleanPhone}...`)

        const code = await newSock.requestPairingCode(cleanPhone)
        pairingCode = code
        connectionStatus = 'waiting_pair'
        console.log(`📱 Pairing code для +${cleanPhone}: ${code}`)

        sock = newSock
        setupConnectionHandlers(sock, false)

        res.json({ success: true, pairingCode: code })

    } catch (error) {
        console.error('❌ Pairing error:', error.message)
        console.error(error.stack)
        res.status(500).json({ error: error.message })
    }
})

app.post('/logout', async (req, res) => {
    try {
        if (sock) {
            try { sock.end(undefined) } catch {}
            sock = null
        }
        if (fs.existsSync(AUTH_DIR)) {
            fs.rmSync(AUTH_DIR, { recursive: true, force: true })
        }
        isConnected = false
        connectionStatus = 'logged_out'
        pairingCode = null
        ensureAuthDir()
        res.json({ success: true })
    } catch (error) {
        res.status(500).json({ error: error.message })
    }
})

app.post('/send', async (req, res) => {
    const { phone, message } = req.body
    if (!phone || !message) {
        return res.status(400).json({ error: 'phone и message обязательны' })
    }
    if (!sock || !isConnected) {
        return res.status(503).json({ error: 'WhatsApp не подключён', status: connectionStatus })
    }
    try {
        const jid = '+' + normalizePhone(phone) + '@s.whatsapp.net'
        await sock.sendMessage(jid, { text: message })
        res.json({ success: true, to: phone })
    } catch (error) {
        console.error('Send error:', error.message)
        res.status(500).json({ error: error.message })
    }
})

app.post('/send_bulk', async (req, res) => {
    const { phones, message, delay = 30 } = req.body
    if (!phones?.length || !message) {
        return res.status(400).json({ error: 'phones[] и message обязательны' })
    }
    if (!sock || !isConnected) {
        return res.status(503).json({ error: 'WhatsApp не подключён', status: connectionStatus })
    }

    const results = { success: true, sent: [], failed: [], total: phones.length }
    const startTime = Date.now()

    for (let i = 0; i < phones.length; i++) {
        const phone = phones[i]
        try {
            const jid = '+' + normalizePhone(phone) + '@s.whatsapp.net'
            await sock.sendMessage(jid, { text: message })
            results.sent.push(phone)
            console.log(`✅ ${i + 1}/${phones.length}: ${phone}`)
        } catch (error) {
            console.error(`❌ ${phone}:`, error.message)
            results.failed.push({ phone, error: error.message })
        }
        if (i < phones.length - 1 && delay > 0) {
            await new Promise(resolve => setTimeout(resolve, delay * 1000))
        }
    }

    results.elapsed_seconds = Math.round((Date.now() - startTime) / 1000)
    res.json(results)
})

app.listen(PORT, '0.0.0.0', () => {
    console.log(`🌐 WA Bridge API запущен на порту ${PORT}`)
})

connectToWhatsApp().catch(console.error)
