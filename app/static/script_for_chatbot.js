// Site chatbot (shared by every community page). Moved out of script_for_home.js.
document.getElementById('chatbotIcon').addEventListener('click', function() {
    // Show the chatbot interface
    document.getElementById('chatbotInterface').style.display = 'flex';
});

document.getElementById('closeChatbot').addEventListener('click', function() {
    // Hide the chatbot interface
    document.getElementById('chatbotInterface').style.display = 'none';
});

// Add a "Who: text" line to the chat log. textContent (not innerHTML) so nothing typed by the
// user or returned by the model can inject HTML into the page.
function addChatLine(who, text) {
    const line = document.createElement('p');
    const label = document.createElement('strong');
    label.textContent = who + ':';
    line.appendChild(label);
    line.appendChild(document.createTextNode(' ' + text));
    const chatLog = document.getElementById('chat-log');
    chatLog.appendChild(line);
    chatLog.scrollTop = chatLog.scrollHeight;
}

function sendChat() {
    const input = document.getElementById('user-input');
    const userInput = input.value;

    if (userInput.trim() === '') return;

    // Display the user's message in the chat log
    addChatLine('You', userInput);

    // Clear the input field
    input.value = '';

    // Send the user's input to the Flask backend
    fetch('/ask', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: userInput })
    })
    .then(response => response.json())
    .then(data => {
        // Display the bot's response in the chat log
        addChatLine('Bot', data.response);
    })
    .catch(error => {
        console.error('Error:', error);
        addChatLine('Bot', 'Sorry, something went wrong.');
    });
}

document.getElementById('send-button').addEventListener('click', sendChat);
document.getElementById('user-input').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') sendChat();
});
