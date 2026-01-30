/**
 * WebAuthn/Passkey Authentication Support
 *
 * Provides client-side functionality for WebAuthn registration and authentication
 * using the Web Authentication API (navigator.credentials).
 */

const WebAuthn = {
    /**
     * Check if WebAuthn is supported by the browser.
     * @returns {boolean}
     */
    isSupported() {
        return window.PublicKeyCredential !== undefined &&
               typeof window.PublicKeyCredential === 'function';
    },

    /**
     * Check if platform authenticator (Touch ID, Face ID, Windows Hello) is available.
     * @returns {Promise<boolean>}
     */
    async isPlatformAuthenticatorAvailable() {
        if (!this.isSupported()) {
            return false;
        }
        try {
            return await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
        } catch (e) {
            console.warn('Platform authenticator check failed:', e);
            return false;
        }
    },

    /**
     * Convert base64url string to Uint8Array.
     * @param {string} base64url
     * @returns {Uint8Array}
     */
    base64urlToBytes(base64url) {
        // Add padding if needed
        const padding = '='.repeat((4 - base64url.length % 4) % 4);
        const base64 = (base64url + padding)
            .replace(/-/g, '+')
            .replace(/_/g, '/');
        const rawData = atob(base64);
        const bytes = new Uint8Array(rawData.length);
        for (let i = 0; i < rawData.length; i++) {
            bytes[i] = rawData.charCodeAt(i);
        }
        return bytes;
    },

    /**
     * Convert Uint8Array to base64url string.
     * @param {Uint8Array} bytes
     * @returns {string}
     */
    bytesToBase64url(bytes) {
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        const base64 = btoa(binary);
        return base64
            .replace(/\+/g, '-')
            .replace(/\//g, '_')
            .replace(/=+$/, '');
    },

    /**
     * Parse WebAuthn options from server response.
     * Converts base64url encoded fields to ArrayBuffers as required by the API.
     * @param {Object} options - Options object from server
     * @returns {Object} - Options ready for navigator.credentials
     */
    parseRegistrationOptions(options) {
        // Parse from JSON string if needed
        if (typeof options === 'string') {
            options = JSON.parse(options);
        }

        // Convert challenge
        if (options.challenge) {
            options.challenge = this.base64urlToBytes(options.challenge);
        }

        // Convert user.id
        if (options.user && options.user.id) {
            options.user.id = this.base64urlToBytes(options.user.id);
        }

        // Convert excludeCredentials
        if (options.excludeCredentials) {
            options.excludeCredentials = options.excludeCredentials.map(cred => ({
                ...cred,
                id: this.base64urlToBytes(cred.id)
            }));
        }

        return options;
    },

    /**
     * Parse authentication options from server response.
     * @param {Object} options - Options object from server
     * @returns {Object} - Options ready for navigator.credentials.get
     */
    parseAuthenticationOptions(options) {
        // Parse from JSON string if needed
        if (typeof options === 'string') {
            options = JSON.parse(options);
        }

        // Convert challenge
        if (options.challenge) {
            options.challenge = this.base64urlToBytes(options.challenge);
        }

        // Convert allowCredentials
        if (options.allowCredentials) {
            options.allowCredentials = options.allowCredentials.map(cred => ({
                ...cred,
                id: this.base64urlToBytes(cred.id)
            }));
        }

        return options;
    },

    /**
     * Encode a credential for sending to the server.
     * @param {PublicKeyCredential} credential
     * @returns {Object}
     */
    encodeRegistrationCredential(credential) {
        const response = credential.response;

        return {
            id: credential.id,
            rawId: this.bytesToBase64url(new Uint8Array(credential.rawId)),
            type: credential.type,
            response: {
                clientDataJSON: this.bytesToBase64url(new Uint8Array(response.clientDataJSON)),
                attestationObject: this.bytesToBase64url(new Uint8Array(response.attestationObject)),
                transports: response.getTransports ? response.getTransports() : []
            },
            authenticatorAttachment: credential.authenticatorAttachment,
            clientExtensionResults: credential.getClientExtensionResults()
        };
    },

    /**
     * Encode an authentication credential for sending to the server.
     * @param {PublicKeyCredential} credential
     * @returns {Object}
     */
    encodeAuthenticationCredential(credential) {
        const response = credential.response;

        return {
            id: credential.id,
            rawId: this.bytesToBase64url(new Uint8Array(credential.rawId)),
            type: credential.type,
            response: {
                clientDataJSON: this.bytesToBase64url(new Uint8Array(response.clientDataJSON)),
                authenticatorData: this.bytesToBase64url(new Uint8Array(response.authenticatorData)),
                signature: this.bytesToBase64url(new Uint8Array(response.signature)),
                userHandle: response.userHandle ?
                    this.bytesToBase64url(new Uint8Array(response.userHandle)) : null
            },
            authenticatorAttachment: credential.authenticatorAttachment,
            clientExtensionResults: credential.getClientExtensionResults()
        };
    },

    /**
     * Start WebAuthn registration.
     * @param {string} token - Verification token from email
     * @param {string} authType - 'passkey' or 'fido2'
     * @returns {Promise<Object>} - Result with challenge for completion
     */
    async startRegistration(token, authType = 'passkey') {
        const response = await fetch('/auth/register/webauthn/begin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token, auth_type: authType })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to start registration');
        }

        return await response.json();
    },

    /**
     * Complete WebAuthn registration using the browser's authenticator.
     * @param {Object} beginResult - Result from startRegistration
     * @param {string} token - Verification token
     * @param {string} authType - 'passkey' or 'fido2'
     * @param {Object} recoveryOptions - Optional recovery email/phone
     * @returns {Promise<Object>} - Registration result with backup codes
     */
    async completeRegistration(beginResult, token, authType, recoveryOptions = {}) {
        // Parse options for the browser API
        const options = this.parseRegistrationOptions(beginResult.options);

        // Create credential using browser authenticator
        let credential;
        try {
            credential = await navigator.credentials.create({
                publicKey: options
            });
        } catch (e) {
            if (e.name === 'NotAllowedError') {
                throw new Error('Registration was cancelled or timed out');
            } else if (e.name === 'InvalidStateError') {
                throw new Error('This device is already registered');
            } else if (e.name === 'NotSupportedError') {
                throw new Error('This authenticator is not supported');
            }
            throw new Error('Failed to create passkey: ' + e.message);
        }

        if (!credential) {
            throw new Error('No credential created');
        }

        // Encode credential for server
        const encodedCredential = this.encodeRegistrationCredential(credential);

        // Send to server for verification
        const response = await fetch('/auth/register/webauthn/complete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                token: token,
                credential: encodedCredential,
                challenge: beginResult.challenge,
                auth_type: authType,
                recovery_email: recoveryOptions.email || null,
                recovery_phone: recoveryOptions.phone || null
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Registration verification failed');
        }

        return await response.json();
    },

    /**
     * Perform full registration flow.
     * @param {string} token - Verification token
     * @param {string} authType - 'passkey' or 'fido2'
     * @param {Object} recoveryOptions - Optional {email, phone}
     * @returns {Promise<Object>} - Registration result
     */
    async register(token, authType = 'passkey', recoveryOptions = {}) {
        // Step 1: Start registration
        const beginResult = await this.startRegistration(token, authType);

        // Step 2: Complete with browser authenticator
        return await this.completeRegistration(beginResult, token, authType, recoveryOptions);
    },

    /**
     * Start WebAuthn authentication.
     * @param {string} username
     * @returns {Promise<Object>} - Result with challenge for completion
     */
    async startAuthentication(username) {
        const response = await fetch('/auth/login/webauthn/begin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to start authentication');
        }

        return await response.json();
    },

    /**
     * Complete WebAuthn authentication.
     * @param {Object} beginResult - Result from startAuthentication
     * @param {string} username
     * @returns {Promise<Object>} - Login result with user info
     */
    async completeAuthentication(beginResult, username) {
        // Parse options for the browser API
        const options = this.parseAuthenticationOptions(beginResult.options);

        // Get credential using browser authenticator
        let credential;
        try {
            credential = await navigator.credentials.get({
                publicKey: options
            });
        } catch (e) {
            if (e.name === 'NotAllowedError') {
                throw new Error('Authentication was cancelled or timed out');
            }
            throw new Error('Failed to authenticate: ' + e.message);
        }

        if (!credential) {
            throw new Error('No credential returned');
        }

        // Encode credential for server
        const encodedCredential = this.encodeAuthenticationCredential(credential);

        // Send to server for verification
        const response = await fetch('/auth/login/webauthn/complete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',  // Important for session cookie
            body: JSON.stringify({
                username: username,
                credential: encodedCredential,
                challenge: beginResult.challenge
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Authentication failed');
        }

        return await response.json();
    },

    /**
     * Perform full authentication flow.
     * @param {string} username
     * @returns {Promise<Object>} - Login result
     */
    async authenticate(username) {
        // Step 1: Start authentication
        const beginResult = await this.startAuthentication(username);

        // Step 2: Complete with browser authenticator
        return await this.completeAuthentication(beginResult, username);
    },

    /**
     * Get the user's authentication type from the server.
     * @param {string} username
     * @returns {Promise<string>} - 'totp', 'passkey', or 'fido2'
     */
    async getAuthType(username) {
        const response = await fetch('/auth/login/auth-type', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username })
        });

        if (!response.ok) {
            return 'totp';  // Default fallback
        }

        const data = await response.json();
        return data.auth_type;
    }
};

// Export for module use
if (typeof module !== 'undefined' && module.exports) {
    module.exports = WebAuthn;
}
