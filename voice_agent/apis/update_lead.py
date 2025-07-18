import os
import asyncio
import aiohttp
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env.local")


async def update_lead(lead_id: str, **kwargs) -> bool:
    """
    Makes an async POST API call to update lead information.

    Args:
        lead_id (str): The lead ID to update (required)
        **kwargs: Optional fields to update (first_name, last_name, title, comments, etc.)

    Returns:
        bool: True if successful, False if error
    """
    # Get password from environment variable
    api_pass = os.environ.get("VICIDIAL_API_PASS")
    if not api_pass:
        raise ValueError("VICIDIAL_API_PASS environment variable not set")

    # API endpoint
    url = "http://fusbot.autodial.tech/vicidial/non_agent_api.php"

    # Build parameters - start with required params
    params = {
        "source": "resembleai",
        "user": "resembleaiapi",
        "pass": api_pass,
        "function": "update_lead",
        "lead_id": lead_id,
    }

    # Add any additional fields passed as kwargs
    # Common fields: first_name, last_name, title, comments, email, phone_number, etc.
    params.update(kwargs)

    try:
        # Create aiohttp session and make POST request
        async with aiohttp.ClientSession() as session:
            async with session.post(url, params=params) as response:
                response.raise_for_status()
                await response.text()  # Read response body
                print(f"Lead updated for: {lead_id}")
                return True

    except aiohttp.ClientError as e:
        print(f"Error making API request: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error: {e}")
        return False


# Example usage
async def main():
    # Example: Update a single lead
    success = await update_lead(
        lead_id="8", first_name="John", title="Lead", comments="success"
    )

    if success:
        print("Lead updated successfully")
    else:
        print("Failed to update lead")

    # Another example with more fields
    success2 = await update_lead(
        lead_id="123",
        first_name="Jane",
        last_name="Doe",
        email="jane.doe@example.com",
        phone_number="5551234567",
        comments="Updated via API",
    )

    if success2:
        print("Lead updated successfully")
    else:
        print("Failed to update lead")


if __name__ == "__main__":
    # Set the environment variable (in production, this would be set externally)
    # os.environ['VICIDIAL_API_PASS'] = 'your_password_here'

    # Run the async main function
    asyncio.run(main())
