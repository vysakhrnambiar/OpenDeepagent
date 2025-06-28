# ===============================================================================
# LEGACY FILE - NOT CURRENTLY USED IN ACTIVE SYSTEM (v16.0+)
# 
# Status: PRESERVED for reference/potential future use
# Last Active: Early development phases (v1-v12)
# Replacement: call_processor_service/call_initiator_svc.py + asterisk_ami_client.py
# Safe to ignore: This file is not imported by main.py or active services
# 
# Historical Context: Original simple outbound call script using direct AMI commands.
#                    Replaced by modular call processor service with proper error
#                    handling, database integration, and lifecycle management.
# ===============================================================================
from asterisk.ami import AMIClient, SimpleAction
from dotenv import load_dotenv
import os
import time

# Load environment variables from .env file
load_dotenv()

ASTERISK_HOST = "192.168.1.24"
ASTERISK_PORT = 5038
AMI_USER = os.getenv("ASTERISK_AMI_USER")
AMI_PASS = os.getenv("ASTERISK_AMI_SECRET")

CALLER_EXTEN = "7000"
TARGET_EXTEN = "1000"
CONTEXT = "default"
CHANNEL_TYPE = "PJSIP"

client = AMIClient(address=ASTERISK_HOST, port=ASTERISK_PORT)
client.login(username=AMI_USER, secret=AMI_PASS)

# Prepare the Originate action
action = SimpleAction(
    'Originate',
    Channel=f'{CHANNEL_TYPE}/{CALLER_EXTEN}',
    Exten=TARGET_EXTEN,
    Context=CONTEXT,
    Priority=1,
    CallerID=f'OpenAI Call <{CALLER_EXTEN}>',
    Timeout=30000,
    Async='true'
)

future = client.send_action(action)

# Wait a moment for AMI to respond
time.sleep(1)

response = future.response
print("Response:", response)

client.logoff()
