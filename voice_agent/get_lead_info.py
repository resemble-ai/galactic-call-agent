import os
import asyncio
import aiohttp
from typing import Dict, Optional, List

from dotenv import load_dotenv

load_dotenv(dotenv_path=".env.local")


async def get_lead_info(phone_number: str) -> Optional[Dict[str, str]]:
    """
    Makes an async GET API call to retrieve lead information based on phone number.

    Args:
        phone_number (str): The phone number to query

    Returns:
        Optional[Dict[str, str]]: First data item as JSON/dict, or None if error
    """
    # Get password from environment variable
    api_pass = os.environ.get("VICIDIAL_API_PASS")

    if not api_pass:
        raise ValueError("VICIDIAL_API_PASS environment variable not set")

    # API endpoint and parameters
    url = "http://fusbot.autodial.tech/vicidial/non_agent_api.php"

    params = {
        "source": "resembleai",
        "user": "resembleaiapi",
        "pass": api_pass,
        "function": "lead_all_info",
        "phone_number": phone_number,
    }

    # Define the field names based on the format provided
    field_names = [
        "status",
        "user",
        "vendor_lead_code",
        "source_id",
        "list_id",
        "gmt_offset_now",
        "phone_code",
        "phone_number",
        "title",
        "first_name",
        "middle_initial",
        "last_name",
        "address1",
        "address2",
        "address3",
        "city",
        "state",
        "province",
        "postal_code",
        "country_code",
        "gender",
        "date_of_birth",
        "alt_phone",
        "email",
        "security_phrase",
        "comments",
        "called_count",
        "last_local_call_time",
        "rank",
        "owner",
        "entry_list_id",
        "lead_id",
    ]

    try:
        # Create aiohttp session and make GET request
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()

                # Read response text
                data = await response.text()
                data = data.strip()

                if not data:
                    return None

                # Split by newline to get multiple data items (if any)
                lines = data.split("\n")

                if not lines:
                    return None

                # Get first line (first data item)
                first_line = lines[0]
                values = first_line.split("|")

                # Create dictionary from field names and values
                if len(values) == len(field_names):
                    result = dict(zip(field_names, values))
                    return result
                else:
                    return None

    except aiohttp.ClientError as e:
        print(f"Error making API request: {e}")
        return None
    except Exception as e:
        print(f"Unexpected error: {e}")
        return None


# Example usage
async def main():
    # Example: Get info for a single lead
    lead_info = await get_lead_info("8052226101")

    if lead_info:
        print(f"Lead found: {lead_info['first_name']} {lead_info['last_name']}")
        print(f"Email: {lead_info['email']}")
        print(f"Lead ID: {lead_info['lead_id']}")
    else:
        print("No lead found for this phone number")


if __name__ == "__main__":
    # Set the environment variable (in production, this would be set externally)
    # os.environ['VICIDIAL_API_PASS'] = 'your_password_here'

    # Run the async main function
    asyncio.run(main())
