document.addEventListener('DOMContentLoaded', () => {
    let username = '';
    let isUsernameLocked = false;

    const chatContainer = document.getElementById('chat-container');
    const chatHeaderTitle = document.getElementById('chat-header-title');
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input');
    const chatBox = document.getElementById('chat-box');
    const themeToggle = document.getElementById('theme-toggle');

    if (!chatContainer || !chatForm) {
        console.error("Chat UI elements not found!");
        return;
    }

    // --- INITIAL SETUP ---
    let chatHistory = [];
    
    function initializeEditableUsername() {
        chatHeaderTitle.innerHTML = 'AI Assistant for <input type="text" id="username-input" value="APPU" />';
    }
    initializeEditableUsername();

    const savedTheme = localStorage.getItem('theme') || 'dark';
    document.body.className = savedTheme + '-theme';

    chatForm.addEventListener('submit', (e) => {
        e.preventDefault();
        sendMessage();
    });

    themeToggle.addEventListener('click', () => {
        document.body.classList.toggle('dark-theme');
        document.body.classList.toggle('light-theme');
        localStorage.setItem('theme', document.body.className.split(' ')[0]);
    });
    
    // --- CORE CHAT FUNCTIONS ---

    function lockUsername() {
        if (!isUsernameLocked) {
            const usernameInputElement = document.getElementById('username-input');
            // This check is the safety net.
            if (usernameInputElement) {
                username = usernameInputElement.value.trim() || 'User';
                chatHeaderTitle.textContent = `AI Assistant for ${username}`;
            }
            isUsernameLocked = true;
        }
    }

    function sendMessage() {
        // --- THIS IS THE FIX ---
        // Lock the username FIRST, before doing anything else.
        lockUsername();
        
        const messageText = chatInput.value.trim();
        if (messageText === '') return;

        const existingForm = document.getElementById('interactive-form-container');
        if (existingForm) {
            existingForm.remove();
        }

        // Now that the username is locked, this will display the correct name.
        appendMessage('user', messageText);
        chatHistory.push({ role: 'user', content: messageText });
        chatInput.value = '';
        sendMessageToApi();
    }

    function appendMessage(sender, text, isHtml = false) {
        const messageElement = document.createElement('div');
        messageElement.classList.add('chat-message', `${sender}-message`);

        const senderElement = document.createElement('div');
        senderElement.classList.add('sender-label');
        senderElement.textContent = sender === 'user' ? username : 'AI Assistant';

        const contentElement = document.createElement('div');
        contentElement.classList.add('message-content');
        if (isHtml) {
            contentElement.innerHTML = text;
        } else {
            contentElement.textContent = text;
        }

        messageElement.appendChild(senderElement);
        messageElement.appendChild(contentElement);
        chatBox.appendChild(messageElement);
        chatBox.scrollTop = chatBox.scrollHeight;
    }

    async function sendMessageToApi() {
        const loadingIndicator = document.createElement('div');
        loadingIndicator.id = 'loading-indicator';
        loadingIndicator.classList.add('chat-message', 'assistant-message');
        loadingIndicator.innerHTML = '<div class="message-content"><div class="typing-indicator"></div></div>';
        chatBox.appendChild(loadingIndicator);
        chatBox.scrollTop = chatBox.scrollHeight;

        try {
            const response = await fetch('/api/chat_interaction', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ chat_history: chatHistory, username: username }),
            });
            const data = await response.json();
            chatHistory.push({ role: 'assistant', content: JSON.stringify(data) });
            renderAssistantMessage(data);

        } catch (error) {
            console.error('Error:', error);
            const loading = document.getElementById('loading-indicator');
            if (loading) loading.remove();
            appendMessage('assistant', 'Sorry, I couldn\'t connect to the server.');
        }
    }
    
    // <editor-fold desc="Rendering and Form Handling Helpers">
    function renderAssistantMessage(response) {
        const loading = document.getElementById('loading-indicator');
        if (loading) loading.remove();

        switch (response.status) {
            case 'clarifying':
                appendMessage('assistant', response.assistant_response);
                break;
            case 'needs_more_info':
                renderInteractiveForm(response.questions);
                break;
            case 'plan_complete':
                renderCampaignPlan(response.campaign_plan);
                break;
            case 'error':
                appendMessage('assistant', `Error: ${response.message}`);
                break;
            default:
                appendMessage('assistant', 'Sorry, I received an unexpected response format.');
        }
    }

    function renderInteractiveForm(questions) {
        let formHtml = `<form id="interactive-form-container" class="interactive-form">`;
        questions.forEach(q => {
            formHtml += `<div class="form-group"><label for="${q.field_name}">${q.question_text}</label>`;
            if (q.response_type === 'textarea') {
                formHtml += `<textarea id="${q.field_name}" name="${q.field_name}" rows="3"></textarea>`;
            } else {
                formHtml += `<input type="text" id="${q.field_name}" name="${q.field_name}">`;
            }
            formHtml += `</div>`;
        });
        formHtml += `<button type="submit">Submit Answers</button></form>`;

        appendMessage('assistant', formHtml, true);

        const interactiveForm = document.getElementById('interactive-form-container');
        if (interactiveForm) {
            interactiveForm.addEventListener('submit', handleFormSubmission);
        }
    }

    function renderCampaignPlan(plan) {
        const planHtml = `
            <div class="campaign-plan">
                <h4>Campaign Plan Ready!</h4>
                <p><strong>Master Prompt:</strong></p>
                <pre>${plan.master_agent_prompt}</pre>
                <p><strong>Contacts:</strong></p>
                <ul>
                    ${plan.contacts.map(c => `<li>${c.name} (${c.phone})</li>`).join('')}
                </ul>
                <button id="confirm-campaign-btn">Confirm and Schedule Campaign</button>
            </div>
        `;
        appendMessage('assistant', planHtml, true);
    }

    function handleFormSubmission(event) {
        event.preventDefault();
        lockUsername(); // Also lock the username if a form is submitted first.
        const form = event.target;
        if (!form) return;

        const formData = new FormData(form);
        let userMessageContent = "Here are the details you asked for:\n\n";
        let hasData = false;

        for (const [name, value] of formData.entries()) {
            if (value.trim() !== "") {
                const label = name.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                userMessageContent += `**${label}:** ${value}\n`;
                hasData = true;
            }
        }

        if (!hasData) return;

        appendMessage('user', userMessageContent);
        chatHistory.push({ role: 'user', content: userMessageContent });
        sendMessageToApi();
        form.remove();
    }
    // </editor-fold>
});