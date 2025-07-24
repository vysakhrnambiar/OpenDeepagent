document.addEventListener('DOMContentLoaded', () => {
    const chatWindow = document.getElementById('chat-window');
    const messageForm = document.getElementById('message-form');
    const messageInput = document.getElementById('message-input');
    const usernameInput = document.getElementById('username-input');
    const micButton = document.getElementById('mic-btn');
    const usernameDisplay = document.getElementById('username-display');
    const editUsernameButton = document.getElementById('edit-username-btn');
    const confirmCampaignButton = document.getElementById('confirm-campaign-btn');
    const toggleThemeButton = document.getElementById('toggle-theme-btn'); // Added

    let conversationHistory = [];
    let currentUsername = "APPU";
    let usernameLocked = false;
    let websocket = null;
    let mediaRecorder = null;
    let audioChunks = [];
    let hitlRequests = new Map(); // Track active HITL requests
    let recordingStartTime;
    let pendingCheckInterval = null; // Interval for checking pending requests
    let notificationIcon = null; // Reference to notification icon

    // Initialize: Input visible, Span hidden
    usernameInput.value = currentUsername;
    usernameInput.classList.remove('hidden');
    editUsernameButton.classList.remove('hidden');
    usernameDisplay.classList.add('hidden');
    usernameDisplay.textContent = currentUsername;
    
    // Show test scenarios panel if username is APPU
    checkTestScenariosVisibility();


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
            checkTestScenariosVisibility();
            console.log("Username pre-set to:", currentUsername);
        }
    });
    
    usernameInput.addEventListener('input', () => {
        if (!usernameLocked) {
            currentUsername = usernameInput.value.trim() || "User";
            checkTestScenariosVisibility();
        }
    });

    // Check if test scenarios should be visible
    function checkTestScenariosVisibility() {
        const testScenariosPanel = document.getElementById('test-scenarios-panel');
        if (testScenariosPanel) {
            if (currentUsername.toUpperCase() === 'APPU') {
                testScenariosPanel.classList.remove('hidden');
            } else {
                testScenariosPanel.classList.add('hidden');
            }
        }
    }

    // Test scenarios data
    const testScenarios = {
        'hotel-booking': {
            title: 'Hotel Booking with Time Conflict',
            description: 'Book a hotel room but requested time unavailable',
            taskDescription: 'Call the Grand Plaza Hotel to book a deluxe room for 2 guests on March 15th, 2024 from 3:00 PM check-in. The booking reference should be confirmed and payment details collected.',
            contacts: [{
                name: 'Grand Plaza Hotel Reception',
                phone: '+1234567890',
                business: 'Grand Plaza Hotel'
            }],
            agentPrompt: 'You are calling to book a hotel room. Ask for a deluxe room for 2 guests on March 15th, 2024 with 3:00 PM check-in. If they say that time is not available, ask what times are available and request human input to decide.',
            hitlEnabled: true,
            hitlTimeout: 30
        },
        'restaurant-reservation': {
            title: 'Restaurant Reservation with Menu Changes',
            description: 'Make dinner reservation but menu items changed',
            taskDescription: 'Call Bella Vista Restaurant to make a reservation for 4 people on Saturday 7:00 PM. Specifically request the chef\'s special tasting menu and confirm dietary restrictions can be accommodated.',
            contacts: [{
                name: 'Bella Vista Restaurant',
                phone: '+1234567891',
                business: 'Bella Vista Restaurant'
            }],
            agentPrompt: 'You are calling to make a dinner reservation for 4 people on Saturday at 7:00 PM. Specifically ask for the chef\'s special tasting menu. If they mention menu changes or unavailable items, ask what alternatives they recommend and request human input for decision.',
            hitlEnabled: true,
            hitlTimeout: 25
        },
        'appointment-scheduling': {
            title: 'Doctor Appointment with Availability Conflict',
            description: 'Schedule appointment but preferred time unavailable',
            taskDescription: 'Call Dr. Smith\'s office to schedule a routine checkup appointment for next Tuesday at 10:00 AM. Confirm insurance coverage and any required preparation.',
            contacts: [{
                name: 'Dr. Smith\'s Office',
                phone: '+1234567892',
                business: 'Smith Medical Practice'
            }],
            agentPrompt: 'You are calling to schedule a routine checkup appointment with Dr. Smith for next Tuesday at 10:00 AM. If that time is not available, ask what times are available this week and request human input to choose the best alternative.',
            hitlEnabled: true,
            hitlTimeout: 20
        },
        'confirmation-call': {
            title: 'Appointment Confirmation Call',
            description: 'Simple confirmation of existing appointment',
            taskDescription: 'Call to confirm an existing appointment scheduled for tomorrow at 2:00 PM. Verify the appointment details and ask if any preparation is needed.',
            contacts: [{
                name: 'City Dental Office',
                phone: '+1234567893',
                business: 'City Dental Practice'
            }],
            agentPrompt: 'You are calling to confirm an existing appointment scheduled for tomorrow at 2:00 PM. Verify the appointment details, confirm the time works, and ask if any preparation is needed.',
            hitlEnabled: false,
            hitlTimeout: 10
        },
        'follow-up-call': {
            title: 'Service Follow-up Call',
            description: 'Check satisfaction after recent service',
            taskDescription: 'Call to follow up on recent home cleaning service. Ask about satisfaction with the service, any issues, and if they would like to schedule regular cleaning.',
            contacts: [{
                name: 'Clean Home Services Customer',
                phone: '+1234567894',
                business: 'Clean Home Services'
            }],
            agentPrompt: 'You are calling to follow up on a recent home cleaning service. Ask about their satisfaction with the service, if there were any issues, and if they would be interested in scheduling regular cleaning services.',
            hitlEnabled: false,
            hitlTimeout: 10
        },
        'survey-call': {
            title: 'Customer Satisfaction Survey',
            description: 'Quick feedback survey call',
            taskDescription: 'Call to conduct a brief customer satisfaction survey about recent online purchase. Ask about delivery experience, product quality, and likelihood to recommend.',
            contacts: [{
                name: 'Recent Online Customer',
                phone: '+1234567895',
                business: 'TechGear Online Store'
            }],
            agentPrompt: 'You are calling to conduct a brief customer satisfaction survey about a recent online purchase. Ask about the delivery experience, product quality, and their likelihood to recommend our store to others. Keep it brief and friendly.',
            hitlEnabled: false,
            hitlTimeout: 10
        }
    };

    // Add event listeners for scenario buttons
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('scenario-btn')) {
            const scenarioId = e.target.dataset.scenario;
            executeTestScenario(scenarioId);
        }
    });

    function executeTestScenario(scenarioId) {
        const scenario = testScenarios[scenarioId];
        if (!scenario) {
            console.error('Test scenario not found:', scenarioId);
            return;
        }

        // Lock username if not already locked
        if (!usernameLocked) {
            lockUsername();
        }

        // Clear chat and show scenario info
        chatWindow.innerHTML = '';
        appendMessage('system', `🧪 Executing Test Scenario: ${scenario.title}`, 'html');
        appendMessage('system', scenario.description, 'html');

        // Create the campaign plan directly in the expected format
        const campaignPlan = {
            user_goal_description: scenario.taskDescription,
            master_agent_prompt: scenario.agentPrompt,
            contacts: scenario.contacts.map(contact => ({
                name: contact.name,
                phone: contact.phone
            }))
        };

        // Show campaign plan
        const planText = `📋 Campaign Plan Generated:
        
📞 Task: ${scenario.taskDescription}
        
👤 Contact: ${scenario.contacts[0].name}
📱 Phone: ${scenario.contacts[0].phone}
🏢 Business: ${scenario.contacts[0].business}
        
🤖 Master Agent Prompt: ${scenario.agentPrompt}
        
${scenario.hitlEnabled ? '🔔 HITL Enabled: Human input may be requested during call' : '🤖 Auto Mode: No human input required'}
        
⏰ Scheduled: Starting in 10 seconds`;

        appendMessage('assistant', planText);
        
        // Store campaign plan and show confirm button
        confirmCampaignButton.dataset.campaignPlan = JSON.stringify(campaignPlan);
        confirmCampaignButton.textContent = `🚀 Start ${scenario.title}`;
        confirmCampaignButton.classList.remove('hidden');

        // Auto-scroll to bottom
        chatWindow.scrollTop = chatWindow.scrollHeight;

        // Hide test scenarios panel after selection
        const testScenariosPanel = document.getElementById('test-scenarios-panel');
        if (testScenariosPanel) {
            testScenariosPanel.classList.add('hidden');
        }

        // Add option to show scenarios again
        setTimeout(() => {
            const showScenariosAgain = document.createElement('div');
            showScenariosAgain.className = 'show-scenarios-again';
            showScenariosAgain.innerHTML = `
                <button class="scenario-btn" onclick="showTestScenariosAgain()" style="margin-top: 10px; border-color: #666; font-size: 0.9em;">
                    🔄 Show Test Scenarios Again
                </button>
            `;
            chatWindow.appendChild(showScenariosAgain);
            chatWindow.scrollTop = chatWindow.scrollHeight;
        }, 1000);
    }

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


    // WebSocket connection for HITL notifications
    function connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/hitl/${currentUsername}`;
        
        websocket = new WebSocket(wsUrl);
        
        websocket.onopen = function(event) {
            console.log('WebSocket connected for HITL notifications');
            showNotification('Connected to real-time notifications', 'success');
        };
        
        websocket.onmessage = function(event) {
            const data = JSON.parse(event.data);
            handleHITLNotification(data);
        };
        
        websocket.onclose = function(event) {
            console.log('WebSocket connection closed');
            showNotification('Disconnected from real-time notifications', 'warning');
            // Attempt to reconnect after 3 seconds
            setTimeout(connectWebSocket, 3000);
        };
        
        websocket.onerror = function(error) {
            console.error('WebSocket error:', error);
            showNotification('Connection error - retrying...', 'error');
        };
    }

    function handleHITLNotification(data) {
        if (data.type === 'hitl_request') {
            showHITLRequest(data);
        } else if (data.type === 'hitl_timeout') {
            // Only show timeout if request still exists (not already responded to)
            if (hitlRequests.has(data.task_id)) {
                showNotification('HITL request timed out - call ended gracefully', 'warning');
                removeHITLRequest(data.task_id);
            }
        }
    }

    function showHITLRequest(requestData) {
        const taskId = requestData.task_id;
        const question = requestData.question;
        const timeoutSeconds = requestData.timeout_seconds;
        const callInfo = requestData.call_info || {};
        
        // Store the request
        hitlRequests.set(taskId, {
            ...requestData,
            startTime: Date.now()
        });
        
        // Create HITL request UI
        const hitlContainer = document.getElementById('hitl-container') || createHITLContainer();
        
        // Make sure container is visible
        hitlContainer.classList.remove('hidden');
        
        const requestElement = document.createElement('div');
        requestElement.className = 'hitl-request urgent';
        requestElement.id = `hitl-request-${taskId}`;
        
        requestElement.innerHTML = `
            <div class="hitl-header">
                <span class="hitl-icon">📞</span>
                <h3>URGENT: Live Call Information Needed</h3>
                <div class="hitl-timer" id="timer-${taskId}">${timeoutSeconds}s</div>
            </div>
            <div class="hitl-call-info">
                <p><strong>Calling:</strong> ${callInfo.phone_number || 'Unknown'}</p>
                <p><strong>Contact:</strong> ${callInfo.person_name || 'Unknown'}</p>
            </div>
            <div class="hitl-question">
                <p><strong>AI Agent Question:</strong></p>
                <p class="question-text">${question}</p>
            </div>
            <div class="hitl-response">
                <textarea id="response-${taskId}" placeholder="Type your response here..." rows="3"></textarea>
                <div class="hitl-actions">
                    <button class="btn btn-primary" onclick="submitHITLResponse('${taskId}')">Send Response</button>
                    <button class="btn btn-secondary" onclick="dismissHITLRequest('${taskId}')">I'll Call Back</button>
                </div>
            </div>
        `;
        
        hitlContainer.appendChild(requestElement);
        
        // Start countdown timer
        startHITLTimer(taskId, timeoutSeconds);
        
        // Show notification
        showNotification(`AI agent needs information during live call!`, 'urgent');
        
        // Auto-focus on response textarea
        document.getElementById(`response-${taskId}`).focus();
    }

    function createHITLContainer() {
        const container = document.createElement('div');
        container.id = 'hitl-container';
        container.className = 'hitl-container';
        document.body.appendChild(container);
        return container;
    }

    function startHITLTimer(taskId, timeoutSeconds) {
        const timerElement = document.getElementById(`timer-${taskId}`);
        const requestData = hitlRequests.get(taskId);
        
        if (!timerElement || !requestData) return;
        
        const interval = setInterval(() => {
            const elapsed = Math.floor((Date.now() - requestData.startTime) / 1000);
            const remaining = Math.max(0, timeoutSeconds - elapsed);
            
            timerElement.textContent = `${remaining}s`;
            
            if (remaining <= 5) {
                timerElement.classList.add('critical');
            } else if (remaining <= 10) {
                timerElement.classList.add('warning');
            }
            
            if (remaining === 0) {
                clearInterval(interval);
                // Don't show timeout notification here, let WebSocket handle it
                removeHITLRequest(taskId);
            }
        }, 1000);
        
        // Store interval for cleanup
        requestData.timerInterval = interval;
        hitlRequests.set(taskId, requestData);
    }

    function submitHITLResponse(taskId) {
        const responseElement = document.getElementById(`response-${taskId}`);
        const response = responseElement.value.trim();
        
        if (!response) {
            showNotification('Please enter a response', 'error');
            responseElement.focus();
            return;
        }
        
        // Send response to backend
        fetch('/api/hitl_response', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                task_id: taskId,
                response: response,
                username: currentUsername
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showNotification('Response sent! Call continuing...', 'success');
                removeHITLRequest(taskId);
            } else {
                showNotification(`Error: ${data.message}`, 'error');
            }
        })
        .catch(error => {
            console.error('Error sending HITL response:', error);
            showNotification('Failed to send response', 'error');
        });
    }

    function dismissHITLRequest(taskId) {
        removeHITLRequest(taskId);
        showNotification('Request dismissed - call will end and reschedule', 'info');
    }

    function removeHITLRequest(taskId) {
        const requestElement = document.getElementById(`hitl-request-${taskId}`);
        if (requestElement) {
            requestElement.remove();
        }
        
        const requestData = hitlRequests.get(taskId);
        if (requestData && requestData.timerInterval) {
            clearInterval(requestData.timerInterval);
        }
        
        hitlRequests.delete(taskId);
        
        // Hide container if no more requests
        const container = document.getElementById('hitl-container');
        if (container && container.children.length === 0) {
            container.classList.add('hidden');
        }
    }

    function showNotification(message, type = 'info') {
        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.innerHTML = `
            <span class="notification-message">${message}</span>
            <button class="notification-close" onclick="this.parentElement.remove()">×</button>
        `;
        
        const container = document.getElementById('notification-container') || createNotificationContainer();
        container.appendChild(notification);
        
        // Auto-remove after 5 seconds (except for urgent notifications)
        if (type !== 'urgent') {
            setTimeout(() => {
                if (notification.parentElement) {
                    notification.remove();
                }
            }, 5000);
        }
    }

    function createNotificationContainer() {
        const container = document.createElement('div');
        container.id = 'notification-container';
        container.className = 'notification-container';
        document.body.appendChild(container);
        return container;
    }

    // Connect WebSocket when username is locked (after first message)
    const originalLockUsername = lockUsername;
    lockUsername = function() {
        originalLockUsername();
        if (!websocket) {
            connectWebSocket();
        }
    };

    // Global functions for HITL (needed for onclick handlers)
    window.submitHITLResponse = submitHITLResponse;
    window.dismissHITLRequest = dismissHITLRequest;

    // Function to show test scenarios again
    window.showTestScenariosAgain = function() {
        const testScenariosPanel = document.getElementById('test-scenarios-panel');
        if (testScenariosPanel && currentUsername.toUpperCase() === 'APPU') {
            testScenariosPanel.classList.remove('hidden');
        }
        
        // Remove the "show again" buttons
        const showAgainButtons = document.querySelectorAll('.show-scenarios-again');
        showAgainButtons.forEach(btn => btn.remove());
    };

    // Initial focus
    messageInput.focus();

    micButton.addEventListener('mousedown', startRecording);
    micButton.addEventListener('mouseup', stopRecording);
    micButton.addEventListener('touchstart', startRecording);
    micButton.addEventListener('touchend', stopRecording);

    async function startRecording() {
        if (mediaRecorder && mediaRecorder.state === 'recording') {
            return;
        }

        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            mediaRecorder = new MediaRecorder(stream);
            mediaRecorder.ondataavailable = event => {
                audioChunks.push(event.data);
            };
            mediaRecorder.onstop = sendAudioForTranscription;
            audioChunks = [];
            mediaRecorder.start();
            recordingStartTime = Date.now();
            micButton.style.backgroundColor = '#ff0000';
        } catch (error) {
            console.error('Error accessing microphone:', error);
            appendMessage('system', 'Could not access microphone. Please ensure permissions are granted.');
        }
    }

    function stopRecording() {
        if (mediaRecorder && mediaRecorder.state === 'recording') {
            mediaRecorder.stop();
            micButton.style.backgroundColor = '';
        }
    }

    async function sendAudioForTranscription() {
        const duration = Date.now() - recordingStartTime;
        if (duration < 200) { // Check for a minimum duration of 200ms
            console.log('Recording too short, not sending.');
            appendMessage('system', 'Recording was too short. Please press and hold the mic button to record.');
            audioChunks = [];
            return;
        }

        if (audioChunks.length === 0) {
            return;
        }

        const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
        const formData = new FormData();
        formData.append('file', audioBlob, 'recording.webm');

        try {
            const response = await fetch('/api/transcribe_audio', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to transcribe audio');
            }

            const result = await response.json();
            if (result.success && result.text) {
                messageInput.value = result.text;
            } else {
                throw new Error('Transcription was not successful.');
            }
        } catch (error) {
            console.error('Error sending audio for transcription:', error);
            appendMessage('system', `Error during transcription: ${error.message}`);
        } finally {
            audioChunks = [];
        }
    }
});