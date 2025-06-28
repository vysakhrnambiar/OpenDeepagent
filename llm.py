# ===============================================================================
# LEGACY FILE - NOT CURRENTLY USED IN ACTIVE SYSTEM (v16.0+)
# 
# Status: PRESERVED for reference/potential future use
# Last Active: Early development phases (v1-v15)
# Replacement: llm_integrations/openai_form_client.py and similar modular LLM clients
# Safe to ignore: This file is not imported by main.py or active services
# 
# Historical Context: Original LLM handler for direct OpenAI GPT-4o integration.
#                    Replaced by modular LLM integration system that supports
#                    multiple providers and specialized clients for different tasks.
# ===============================================================================
#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
LLM Module

This module handles integration with OpenAI's GPT-4o for conversation management.
"""

import logging
import time
import json
import openai

# Get the logger
logger = logging.getLogger(__name__)


class LLMHandler:
    """Handler for LLM integration."""
    
    def __init__(self, config):
        """
        Initialize the LLM handler.
        
        Args:
            config (Config): The application configuration
        """
        self.config = config
        self.client = openai.OpenAI(api_key=config.openai_api_key)
        self.model = "gpt-4o"
        self.conversation_history = []
        self.system_prompt = config.get_system_prompt()
        
        # Initialize conversation with system message
        self.conversation_history.append({
            "role": "system",
            "content": self.system_prompt
        })
        
        logger.debug(f"LLM handler initialized with model: {self.model}")
        
    def add_user_message(self, message):
        """
        Add a user message to the conversation history.
        
        Args:
            message (str): The user message
        """
        self.conversation_history.append({
            "role": "user",
            "content": message
        })
        logger.debug(f"Added user message: {message[:50]}...")
        
    def add_assistant_message(self, message):
        """
        Add an assistant message to the conversation history.
        
        Args:
            message (str): The assistant message
        """
        self.conversation_history.append({
            "role": "assistant",
            "content": message
        })
        logger.debug(f"Added assistant message: {message[:50]}...")
        
    def get_last_assistant_message(self):
        """
        Get the last assistant message from the conversation history.
        
        Returns:
            str: The last assistant message, or None if no assistant messages exist
        """
        for message in reversed(self.conversation_history):
            if message["role"] == "assistant":
                return message["content"]
        return None
        
    def get_response(self, temperature=0.7, max_tokens=None):
        """
        Get a response from the LLM.
        
        Args:
            temperature (float): The temperature for response generation
            max_tokens (int, optional): Maximum number of tokens to generate
            
        Returns:
            str: The LLM response
        """
        try:
            start_time = time.time()
            
            # Call the OpenAI API
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.conversation_history,
                temperature=temperature,
                max_tokens=max_tokens
            )
            
            # Get the response text
            response_text = response.choices[0].message.content
            
            # Add the response to the conversation history
            self.add_assistant_message(response_text)
            
            elapsed_time = time.time() - start_time
            logger.debug(f"LLM response generated in {elapsed_time:.2f} seconds")
            
            return response_text
            
        except Exception as e:
            logger.error(f"Error getting LLM response: {str(e)}")
            return "I'm sorry, I'm having trouble generating a response right now."
            
    def analyze_conversation(self, goal_completion_prompt=None):
        """
        Analyze the conversation to determine if goals have been met.
        
        Args:
            goal_completion_prompt (str, optional): Custom prompt for goal completion analysis
            
        Returns:
            dict: Analysis results including goal completion status
        """
        try:
            # Create a prompt for analyzing the conversation
            if not goal_completion_prompt:
                goal_completion_prompt = (
                    "Based on the conversation so far and your instructions, "
                    "please analyze whether the conversation goals have been met. "
                    "Respond with a JSON object containing the following fields:\n"
                    "1. 'goals_completed': true/false - whether all goals have been completed\n"
                    "2. 'can_end_call': true/false - whether the call can be ended now\n"
                    "3. 'reason': string - brief explanation of your decision\n"
                    "4. 'next_steps': string - what should happen next if the call continues"
                )
                
            # Add the analysis prompt as a user message
            analysis_messages = self.conversation_history.copy()
            analysis_messages.append({
                "role": "user",
                "content": goal_completion_prompt
            })
            
            # Call the OpenAI API for analysis
            response = self.client.chat.completions.create(
                model=self.model,
                messages=analysis_messages,
                temperature=0.2  # Lower temperature for more consistent analysis
            )
            
            # Get the response text
            response_text = response.choices[0].message.content
            
            # Parse the JSON response
            try:
                # Try to extract JSON from the response if it's not pure JSON
                if not response_text.strip().startswith('{'):
                    import re
                    json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
                    if json_match:
                        response_text = json_match.group(1)
                    else:
                        # Try to find any JSON object in the response
                        json_match = re.search(r'(\{.*\})', response_text, re.DOTALL)
                        if json_match:
                            response_text = json_match.group(1)
                
                analysis = json.loads(response_text)
                logger.debug(f"Conversation analysis: {analysis}")
                return analysis
            except json.JSONDecodeError:
                logger.error(f"Failed to parse analysis response as JSON: {response_text}")
                # Return a default analysis
                return {
                    "goals_completed": False,
                    "can_end_call": False,
                    "reason": "Failed to parse analysis response",
                    "next_steps": "Continue the conversation"
                }
                
        except Exception as e:
            logger.error(f"Error analyzing conversation: {str(e)}")
            # Return a default analysis
            return {
                "goals_completed": False,
                "can_end_call": False,
                "reason": f"Error: {str(e)}",
                "next_steps": "Continue the conversation"
            }
            
    def save_conversation(self, file_path):
        """
        Save the conversation history to a file.
        
        Args:
            file_path (str): The path to save the conversation
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with open(file_path, 'w') as f:
                json.dump(self.conversation_history, f, indent=2)
            logger.debug(f"Saved conversation to {file_path}")
            return True
        except Exception as e:
            logger.error(f"Error saving conversation: {str(e)}")
            return False