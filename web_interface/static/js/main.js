document.addEventListener('DOMContentLoaded', function() {
    const chatHistoryEl = document.getElementById('chat-history');
    const chatInputEl = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');
    const usernameEl = document.getElementById('username');

    let chatHistory = [];
    let isWaitingForResponse = false;

    // --- Message Rendering Functions ---

    function addMessageToHistory(role, content, isHtml = false) {
        // This function is the single source of truth for adding messages.
        // It adds to the internal state (chatHistory array) AND the UI.
        chatHistory.push({ role: role, content: content });

        const messageDiv = document.createElement('div');
        messageDiv.className = `chat-message ${role}`;
        
        const bubbleDiv = document.createElement('div');
        bubbleDiv.className = `message-bubble ${role}`;
        
        if (isHtml) {
            bubbleDiv.innerHTML = content;
        } else {
            // Sanitize plain text content to prevent XSS by setting it as text, not HTML
            bubbleDiv.innerText = content;
        }
        
        messageDiv.appendChild(bubbleDiv);
        chatHistoryEl.appendChild(messageDiv);
        scrollToBottom();
    }

    function renderAssistantResponse(response) {
        // Add the structured JSON response to our internal history for context later
        chatHistory.push({ role: 'assistant', content: JSON.stringify(response) });

        if (response.status === 'clarifying') {
            // Display the assistant's text response as a new message bubble
            addMessageToHistory('assistant', response.assistant_response);
        
        } else if (response.status === 'needs_more_info') {
            const formHtml = createInteractiveForm(response.questions);
            addMessageToHistory('assistant', formHtml, true);
            const form = chatHistoryEl.querySelector('form:last-of-type');
            if(form) form.addEventListener('submit', handleFormSubmit);

        } else if (response.status === 'plan_complete') {
            const planHtml = createFinalPlanDisplay(response.campaign_plan);
            addMessageToHistory('assistant', planHtml, true);
            const confirmBtn = document.getElementById('confirm-campaign-btn');
            if(confirmBtn) confirmBtn.addEventListener('click', handleConfirmCampaign);
        } else {
            addMessageToHistory('assistant', `I seem to have encountered an unexpected issue. Status: ${response.status || 'unknown'}`);
        }
    }

    function createInteractiveForm(questions) {
        let formHtml = `<form class="interactive-form"><p>I need a bit more information to continue planning:</p>`;
        questions.forEach(q => {
            formHtml += `<div class="form-group">
                           <label for="${q.field_name}">${q.question_text}</label>`;
            if (q.response_type === 'textarea') {
                formHtml += `<textarea id="${q.field_name}" name="${q.field_name}"></textarea>`;
            } else {
                const inputType = q.response_type === 'datetimepicker' ? 'datetime-local' : 'text';
                formHtml += `<input type="${inputType}" id="${q.field_name}" name="${q.field_name}">`;
            }
            formHtml += `</div>`;
        });
        formHtml += `<button type="submit">Submit Answers</button></form>`;
        return formHtml;
    }

    function createFinalPlanDisplay(plan) {
        const sanitizedPrompt = escapeHtml(plan.master_agent_prompt);
        const sanitizedContacts = escapeHtml(JSON.stringify(plan.contacts, null, 2));

        return `<div class="final-plan">
                    <h3>Campaign Plan Ready!</h3>
                    <p>Here is the final plan based on our conversation. Please review it.</p>
                    <strong>Agent Instructions:</strong>
                    <pre>${sanitizedPrompt}</pre>
                    <strong>Contacts to Call:</strong>
                    <pre>${sanitizedContacts}</pre>
                    <button id="confirm-campaign-btn">Confirm and Schedule Campaign</button>
                </div>`;
    }

    // --- Event Handlers ---

    async function handleSend() {
        const userInput = chatInputEl.value.trim();
        if (!userInput || isWaitingForResponse) return;

        addMessageToHistory('user', userInput); // This now adds to UI and history array
        chatInputEl.value = '';
        await getAssistantResponse();
    }
    
    async function handleFormSubmit(event) {
        event.preventDefault();
        const formData = new FormData(event.target);
        let userResponseText = "Here are the details you asked for:\n";
        for (const [key, value] of formData.entries()) {
            if (value) {
                userResponseText += `- ${key.replace(/_/g, ' ')}: ${value}\n`;
            }
        }
        
        // Disable the form that was just submitted to prevent re-clicks
        event.target.querySelectorAll('input, textarea, button').forEach(el => el.disabled = true);

        // Treat the submitted form data as a new user message turn
        addMessageToHistory('user', userResponseText);
        await getAssistantResponse();
    }
    
    function handleConfirmCampaign(event) {
        addMessageToHistory('assistant', "Great! Campaign scheduling will be implemented in the next phase. For now, this step is confirmed.");
        
        // Disable the confirmation button after it's clicked
        const confirmBtn = event.target;
        confirmBtn.innerText = "Confirmed!";
        confirmBtn.disabled = true;
    }


    // --- API Call & UI State ---

    async function getAssistantResponse() {
        if (isWaitingForResponse) return;

        isWaitingForResponse = true;
        showSpinner();

        try {
            const response = await fetch('/api/chat_interaction', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username: usernameEl.value.trim() || 'default_user',
                    chat_history: chatHistory
                })
            });

            hideSpinner();

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'API request failed');
            }

            const data = await response.json();
            renderAssistantResponse(data);

        } catch (error) {
            hideSpinner();
            addMessageToHistory('assistant', `Sorry, an error occurred: ${error.message}`);
        } finally {
            isWaitingForResponse = false;
        }
    }

    function showSpinner() {
        const spinnerDiv = document.createElement('div');
        spinnerDiv.className = 'chat-message assistant';
        spinnerDiv.innerHTML = `<div class="message-bubble assistant"><div class="spinner"></div></div>`;
        spinnerDiv.id = 'thinking-spinner';
        chatHistoryEl.appendChild(spinnerDiv);
        scrollToBottom();
    }

    function hideSpinner() {
        const spinnerDiv = document.getElementById('thinking-spinner');
        if (spinnerDiv) spinnerDiv.remove();
    }
    
    function scrollToBottom() {
        chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
    }
    
    function escapeHtml(unsafe) {
        if (typeof unsafe !== 'string') return '';
        return unsafe
             .replace(/&/g, "&amp;")
             .replace(/</g, "&lt;")
             .replace(/>/g, "&gt;")
             .replace(/"/g, "&quot;")
             .replace(/'/g, "&#39;");
    }

    // --- Initial Setup ---
    sendBtn.addEventListener('click', handleSend);
    chatInputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    });
    
    // Initial greeting
    addMessageToHistory('assistant', 'Hello! I am your campaign assistant. How can I help you today?');
});