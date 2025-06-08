document.addEventListener('DOMContentLoaded', () => {
    const chatWindow = document.getElementById('chat-window');
    const messageForm = document.getElementById('message-form');
    const messageInput = document.getElementById('message-input');
    const usernameInput = document.getElementById('username-input');
    const usernameDisplay = document.getElementById('username-display');
    const editUsernameButton = document.getElementById('edit-username-btn');
    const confirmCampaignButton = document.getElementById('confirm-campaign-btn');
    const toggleThemeButton = document.getElementById('toggle-theme-btn'); // Added

    let conversationHistory = [];
    let currentUsername = "APPU"; 
    let usernameLocked = false;

    // Initialize: Input visible, Span hidden
    usernameInput.value = currentUsername;
    usernameInput.classList.remove('hidden');
    editUsernameButton.classList.remove('hidden');
    usernameDisplay.classList.add('hidden');
    usernameDisplay.textContent = currentUsername;


    function lockUsername() {
        usernameLocked = true;
        currentUsername = usernameInput.value.trim() || "User"; 
        usernameInput.classList.add('hidden');
        editUsernameButton.classList.add('hidden');
        usernameDisplay.textContent = currentUsername;
        usernameDisplay.classList.remove('hidden');
    }

    // This function might not be used if username is locked permanently after first message
    function unlockUsername() { 
        usernameLocked = false;
        usernameInput.classList.remove('hidden');
        editUsernameButton.classList.remove('hidden');
        usernameDisplay.classList.add('hidden');
    }

    editUsernameButton.addEventListener('click', () => {
        if (!usernameLocked) {
            currentUsername = usernameInput.value.trim() || "User";
            // No need to toggle visibility here, just update the variable.
            // lockUsername will handle visibility on first message send.
            console.log("Username pre-set to:", currentUsername);
        }
    });
    
    usernameInput.addEventListener('input', () => {
        if (!usernameLocked) {
            currentUsername = usernameInput.value.trim() || "User";
        }
    });

    if (toggleThemeButton) { // Check if button exists
        toggleThemeButton.addEventListener('click', () => {
            document.body.classList.toggle('dark-theme');
            // Optionally, save theme preference to localStorage
            if (document.body.classList.contains('dark-theme')) {
                localStorage.setItem('theme', 'dark');
            } else {
                localStorage.setItem('theme', 'light');
            }
        });
    }
     // Apply saved theme on load
    if (localStorage.getItem('theme') === 'dark') {
        document.body.classList.add('dark-theme');
    }


    function appendMessage(sender, message, type = 'text') {
        const messageElement = document.createElement('div');
        messageElement.classList.add('message', `${sender}-message`);

        if (type === 'html') {
            messageElement.innerHTML = message;
        } else {
            message.split('\n').forEach((line, index) => {
                if (index > 0) messageElement.appendChild(document.createElement('br'));
                messageElement.appendChild(document.createTextNode(line));
            });
        }
        chatWindow.appendChild(messageElement);
        chatWindow.scrollTop = chatWindow.scrollHeight;
    }

    async function sendMessage(userMessage) {
        if (!userMessage.trim()) return;

        if (!usernameLocked) {
            lockUsername(); 
        }

        appendMessage('user', userMessage);
        conversationHistory.push({ "role": "user", "content": userMessage });

        const thinkingMessage = document.createElement('div');
        thinkingMessage.classList.add('message', 'assistant-message', 'thinking');
        thinkingMessage.textContent = 'Thinking...';
        chatWindow.appendChild(thinkingMessage);
        chatWindow.scrollTop = chatWindow.scrollHeight;

        try {
            const response = await fetch('/api/chat_interaction', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username: currentUsername,
                    message: userMessage,
                    history: conversationHistory.slice(0, -1) 
                })
            });

            chatWindow.removeChild(thinkingMessage);

            if (!response.ok) {
                const errorData = await response.json();
                appendMessage('assistant', `Error: ${errorData.detail || response.statusText}`);
                console.error("API Error:", errorData);
                return;
            }

            const data = await response.json();
            handleAssistantResponse(data);

        } catch (error) {
            chatWindow.removeChild(thinkingMessage);
            appendMessage('assistant', 'Network error. Could not reach the server.');
            console.error('Network error:', error);
        }
    }

    function handleAssistantResponse(data) {
        let assistantResponseText = "";

        if (data.status === 'clarifying') {
            assistantResponseText = data.assistant_response;
            appendMessage('assistant', assistantResponseText);
            conversationHistory.push({ "role": "assistant", "content": assistantResponseText });
            confirmCampaignButton.classList.add('hidden');
        } else if (data.status === 'needs_more_info') {
            const formHtml = createFormFromQuestions(data.questions);
            appendMessage('assistant', formHtml, 'html');
            conversationHistory.push({ "role": "assistant", "content": JSON.stringify(data.questions) });
            confirmCampaignButton.classList.add('hidden');
        } else if (data.status === 'plan_complete') {
            assistantResponseText = "Here is the finalized campaign plan:\n" +
                JSON.stringify(data.campaign_plan, null, 2);
            appendMessage('assistant', assistantResponseText);
            conversationHistory.push({ "role": "assistant", "content": assistantResponseText });
            confirmCampaignButton.dataset.campaignPlan = JSON.stringify(data.campaign_plan);
            confirmCampaignButton.classList.remove('hidden');
        } else if (data.status === 'tool_executed') {
            assistantResponseText = `Tool execution result: ${data.tool_result}\n\n${data.assistant_response}`;
            appendMessage('assistant', assistantResponseText);
            conversationHistory.push({ "role": "assistant", "content": assistantResponseText });
            confirmCampaignButton.classList.add('hidden');
        } else if (data.status === 'error') {
            assistantResponseText = `Error: ${data.message}`;
            appendMessage('assistant', assistantResponseText);
        } else {
            assistantResponseText = "Received an unexpected response from the assistant.";
            appendMessage('assistant', assistantResponseText);
        }
    }

    function createFormFromQuestions(questions) {
        let formHtml = '<form id="dynamic-info-form">';
        questions.forEach((q, index) => {
            formHtml += `
                <div class="form-group">
                    <label for="q-${index}">${q.question_text}</label>`;
            if (q.response_type === 'textarea') {
                formHtml += `<textarea id="q-${index}" name="${q.field_name}" rows="3"></textarea>`;
            } else if (q.response_type === 'select' && q.options) {
                formHtml += `<select id="q-${index}" name="${q.field_name}">`;
                q.options.forEach(opt => {
                    formHtml += `<option value="${opt.value}">${opt.label}</option>`;
                });
                formHtml += `</select>`;
            }
            else { 
                formHtml += `<input type="text" id="q-${index}" name="${q.field_name}">`;
            }
            formHtml += `</div>`;
        });
        formHtml += '<button type="submit" class="form-submit-btn">Submit Answers</button></form>';
        return formHtml;
    }

    messageForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const userMessage = messageInput.value;
        sendMessage(userMessage);
        messageInput.value = '';
    });

    chatWindow.addEventListener('submit', async (e) => {
        if (e.target && e.target.id === 'dynamic-info-form') {
            e.preventDefault();
            const formData = new FormData(e.target);
            const answers = {};
            let allAnswersCombined = "Here are the answers to your questions:\n";

            formData.forEach((value, key) => {
                answers[key] = value;
                const label = e.target.querySelector(`label[for="${e.target.querySelector(`[name='${key}']`).id}"]`);
                const questionText = label ? label.textContent : key;
                allAnswersCombined += `- ${questionText}: ${value}\n`;
            });
            e.target.remove();
            sendMessage(allAnswersCombined.trim());
        }
    });

    confirmCampaignButton.addEventListener('click', async () => {
        const campaignPlanString = confirmCampaignButton.dataset.campaignPlan;
        if (!campaignPlanString) {
            appendMessage('assistant', "Error: No campaign plan found to confirm.");
            return;
        }

        const campaignPlan = JSON.parse(campaignPlanString);
        confirmCampaignButton.classList.add('hidden'); 
        appendMessage('user', "Confirm and Schedule Campaign"); 

        const thinkingMessage = document.createElement('div');
        thinkingMessage.classList.add('message', 'assistant-message', 'thinking');
        thinkingMessage.textContent = 'Scheduling campaign...';
        chatWindow.appendChild(thinkingMessage);
        chatWindow.scrollTop = chatWindow.scrollHeight;

        try {
            const response = await fetch('/api/execute_campaign', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username: currentUsername,
                    campaign_plan: campaignPlan
                })
            });

            chatWindow.removeChild(thinkingMessage);

            const result = await response.json();
            if (response.ok && result.status === 'success') {
                appendMessage('assistant', `Campaign scheduled successfully! ${result.message || ''}`);
                conversationHistory.push({ "role": "assistant", "content": `Campaign scheduled successfully! ${result.message || ''}` });
            } else {
                appendMessage('assistant', `Error scheduling campaign: ${result.detail || result.message || 'Unknown error'}`);
            }
        } catch (error) {
            chatWindow.removeChild(thinkingMessage);
            appendMessage('assistant', 'Network error. Could not schedule the campaign.');
            console.error('Error executing campaign:', error);
        }
    });
    
    // Theme toggle logic (added back)
    if (toggleThemeButton) {
        toggleThemeButton.addEventListener('click', () => {
            document.body.classList.toggle('dark-theme');
            if (document.body.classList.contains('dark-theme')) {
                localStorage.setItem('theme', 'dark');
            } else {
                localStorage.setItem('theme', 'light');
            }
        });
    }
    if (localStorage.getItem('theme') === 'dark') {
        document.body.classList.add('dark-theme');
    }


    // Initial focus
    messageInput.focus();
});