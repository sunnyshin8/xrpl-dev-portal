# "Build a Wallet" tutorial, step 4: Show transaction history
# This step adds a tab that summarizes transactions the user's account has been
# affected by recently, including transactions sent, received, or otherwise
# impacting the user's account.
# License: MIT. https://github.com/XRPLF/xrpl-dev-portal/blob/master/LICENSE

import xrpl
import wx
import wx.dataview
import wx.adv
import asyncio
from threading import Thread
from decimal import Decimal

class XRPLMonitorThread(Thread):
    """
    A worker thread to watch for new ledger events and pass the info back to
    the main frame to be shown in the UI. Using a thread lets us maintain the
    responsiveness of the UI while doing work in the background.
    """
    def __init__(self, url, gui, loop):
        Thread.__init__(self, daemon=True)
        # Note: For thread safety, this thread should treat self.gui as
        # read-only; to modify the GUI, use wx.CallAfter(...)
        self.gui = gui
        self.url = url
        self.loop = loop

    def run(self):
        """
        This thread runs a never-ending event-loop that monitors messages coming
        from the XRPL, sending them to the GUI thread when necessary, and also
        handles making requests to the XRPL when the GUI prompts them.
        """
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def watch_xrpl_account(self, address, wallet=None):
        """
        This is the task that opens the connection to the XRPL, then handles
        incoming subscription messages by dispatching them to the appropriate
        part of the GUI.
        """
        self.account = address
        self.wallet = wallet

        async with xrpl.asyncio.clients.AsyncWebsocketClient(self.url) as self.client:
            await self.on_connected()
            async for message in self.client:
                mtype = message.get("type")
                if mtype == "ledgerClosed":
                    wx.CallAfter(self.gui.update_ledger, message)
                elif mtype == "transaction":
                    wx.CallAfter(self.gui.add_tx_from_sub, message)
                    response = await self.client.request(xrpl.models.requests.AccountInfo(
                        account=self.account,
                        ledger_index=message["ledger_index"]
                    ))
                    wx.CallAfter(self.gui.update_account, response.result["account_data"])

    async def on_connected(self):
        """
        Set up initial subscriptions and populate the GUI with data from the
        ledger on startup. Requires that self.client be connected first.
        """
        # Set up 2 subscriptions: all new ledgers, and any new transactions that
        # affect the chosen account.
        response = await self.client.request(xrpl.models.requests.Subscribe(
            streams=["ledger"],
            accounts=[self.account]
        ))
        # The immediate response contains details for the last validated ledger.
        # We can use this to fill in that area of the GUI without waiting for a
        # new ledger to close.
        wx.CallAfter(self.gui.update_ledger, response.result)

        # Get starting values for account info.
        response = await self.client.request(xrpl.models.requests.AccountInfo(
            account=self.account,
            ledger_index="validated"
        ))
        if not response.is_successful():
            print("Got error from server:", response)
            # This most often happens if the account in question doesn't exist
            # on the network we're connected to. Better handling would be to use
            # wx.CallAfter to display an error dialog in the GUI and possibly
            # let the user try inputting a different account.
            exit(1)
        wx.CallAfter(self.gui.update_account, response.result["account_data"])
        # Get the first page of the account's transaction history. Depending on
        # the server we're connected to, the account's full history may not be
        # available.
        response = await self.client.request(xrpl.models.requests.AccountTx(
            account=self.account
        ))
        wx.CallAfter(self.gui.update_account_tx, response.result)


class AutoGridBagSizer(wx.GridBagSizer):
    """
    Helper class for adding a bunch of items uniformly to a GridBagSizer.
    """
    def __init__(self, parent):
        wx.GridBagSizer.__init__(self, vgap=5, hgap=5)
        self.parent = parent

    def BulkAdd(self, ctrls):
        """
        Given a two-dimensional iterable `ctrls`, add all the items in a grid
        top-to-bottom, left-to-right, with each inner iterable being a row. Set
        the total number of columns based on the longest iterable.
        """
        flags = wx.EXPAND|wx.ALL|wx.RESERVE_SPACE_EVEN_IF_HIDDEN|wx.ALIGN_CENTER_VERTICAL
        for x, row in enumerate(ctrls):
            for y, ctrl in enumerate(row):
                self.Add(ctrl, (x,y), flag=flags, border=5)
        self.parent.SetSizer(self)


class TWaXLFrame(wx.Frame):
    """
    Tutorial Wallet for the XRP Ledger (TWaXL)
    user interface, main frame.
    """
    def __init__(self, url, test_network=True):
        wx.Frame.__init__(self, None, title="TWaXL", size=wx.Size(800,400))

        self.test_network = test_network
        # The ledger's current reserve settings. To be filled in later.
        self.reserve_base = None
        self.reserve_inc = None

        self.build_ui()

        # Pop up to ask user for their account ---------------------------------
        address, wallet = self.prompt_for_account()
        self.classic_address = address

        # Start background thread for updates from the ledger ------------------
        self.worker_loop = asyncio.new_event_loop()
        self.worker = XRPLMonitorThread(url, self, self.worker_loop)
        self.worker.start()
        self.run_bg_job(self.worker.watch_xrpl_account(address, wallet))

    def build_ui(self):
        """
        Called during __init__ to set up all the GUI components.
        """
        self.tabs = wx.Notebook(self, style=wx.BK_DEFAULT)
        # Tab 1: "Summary" pane ------------------------------------------------
        main_panel = wx.Panel(self.tabs)
        self.tabs.AddPage(main_panel, "Summary")

        self.acct_info_area = wx.StaticBox(main_panel, label="Account Info")

        lbl_address = wx.StaticText(self.acct_info_area, label="Classic Address:")
        self.st_classic_address = wx.StaticText(self.acct_info_area, label="TBD")
        lbl_xaddress = wx.StaticText(self.acct_info_area, label="X-Address:")
        self.st_x_address = wx.StaticText(self.acct_info_area, label="TBD")
        lbl_xrp_bal = wx.StaticText(self.acct_info_area, label="XRP Balance:")
        self.st_xrp_balance = wx.StaticText(self.acct_info_area, label="TBD")
        lbl_reserve = wx.StaticText(self.acct_info_area, label="XRP Reserved:")
        self.st_reserve = wx.StaticText(self.acct_info_area, label="TBD")

        aia_sizer = AutoGridBagSizer(self.acct_info_area)
        aia_sizer.BulkAdd( ((lbl_address, self.st_classic_address),
                           (lbl_xaddress, self.st_x_address),
                           (lbl_xrp_bal, self.st_xrp_balance),
                           (lbl_reserve, self.st_reserve)) )

        self.ledger_info = wx.StaticText(main_panel, label="Not connected")

        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(self.acct_info_area, 1, flag=wx.EXPAND|wx.ALL, border=5)
        main_sizer.Add(self.ledger_info, 1, flag=wx.EXPAND|wx.ALL, border=5)
        main_panel.SetSizer(main_sizer)

        # Tab 2: "Transaction History" pane ------------------------------------
        objs_panel = wx.Panel(self.tabs)
        self.tabs.AddPage(objs_panel, "Transaction History")
        objs_sizer = wx.BoxSizer(wx.VERTICAL)

        self.tx_list = wx.dataview.DataViewListCtrl(objs_panel)
        self.tx_list.AppendTextColumn("Confirmed")
        self.tx_list.AppendTextColumn("Type")
        self.tx_list.AppendTextColumn("From")
        self.tx_list.AppendTextColumn("To")
        self.tx_list.AppendTextColumn("Value Delivered")
        self.tx_list.AppendTextColumn("Identifying Hash")
        self.tx_list.AppendTextColumn("Raw JSON")
        objs_sizer.Add(self.tx_list, 1, wx.EXPAND|wx.ALL)

        objs_panel.SetSizer(objs_sizer)

    def run_bg_job(self, job):
        """
        Schedules a job to run asynchronously in the XRPL worker thread.
        The job should be a Future (for example, from calling an async function)
        """
        task = asyncio.run_coroutine_threadsafe(job, self.worker_loop)

    def toggle_dialog_style(self, event):
        """
        Automatically switches to a password-style dialog if it looks like the
        user is entering a secret key, and display ***** instead of s12345...
        """
        dlg = event.GetEventObject()
        v = dlg.GetValue().strip()
        if v[:1] == "s":
            dlg.SetWindowStyle(wx.TE_PASSWORD)
        else:
            dlg.SetWindowStyle(wx.TE_LEFT)

    def prompt_for_account(self):
        """
        Prompt the user for an account to use, in a base58-encoded format:
        - master key seed: Grants read-write access.
          (assumes the master key pair is not disabled)
        - classic address. Grants read-only access.
        - X-address. Grants read-only access.

        Exits with error code 1 if the user cancels the dialog, if the input
        doesn't match any of the formats, or if the user inputs an X-address
        intended for use on a different network type (test/non-test).

        Populates the classic address and X-address labels in the UI.

        Returns (classic_address, wallet) where wallet is None in read-only mode
        """
        account_dialog = wx.TextEntryDialog(self,
                "Please enter an account address (for read-only)"
                " or your secret (for read-write access)",
                caption="Enter account",
                value="rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe")
        account_dialog.Bind(wx.EVT_TEXT, self.toggle_dialog_style)

        if account_dialog.ShowModal() != wx.ID_OK:
            # If the user presses Cancel on the account entry, exit the app.
            exit(1)

        value = account_dialog.GetValue().strip()
        account_dialog.Destroy()

        classic_address = ""
        wallet = None
        x_address = ""

        if xrpl.core.addresscodec.is_valid_xaddress(value):
            x_address = value
            classic_address, dest_tag, test_network = xrpl.core.addresscodec.xaddress_to_classic_address(value)
            if test_network != self.test_network:
                on_net = "a test network" if self.test_network else "Mainnet"
                print(f"X-address {value} is meant for a different network type"
                      f"than this client is connected to."
                      f"(Client is on: {on_net})")
                exit(1)

        elif xrpl.core.addresscodec.is_valid_classic_address(value):
            classic_address = value
            x_address = xrpl.core.addresscodec.classic_address_to_xaddress(
                    value, tag=None, is_test_network=self.test_network)

        else:
            try:
                # Check if it's a valid seed
                seed_bytes, alg = xrpl.core.addresscodec.decode_seed(value)
                wallet = xrpl.wallet.Wallet(seed=value, sequence=0)
                x_address = wallet.get_xaddress(is_test=self.test_network)
                classic_address = wallet.classic_address
            except Exception as e:
                print(e)
                exit(1)

        # Update the UI with the address values
        self.st_classic_address.SetLabel(classic_address)
        self.st_x_address.SetLabel(x_address)

        return classic_address, wallet

    def update_ledger(self, message):
        """
        Process a ledger subscription message to update the UI with
        information about the latest validated ledger.
        """
        close_time_iso = xrpl.utils.ripple_time_to_datetime(message["ledger_time"]).isoformat()
        self.ledger_info.SetLabel(f"Latest validated ledger:\n"
                         f"Ledger Index: {message['ledger_index']}\n"
                         f"Ledger Hash: {message['ledger_hash']}\n"
                         f"Close time: {close_time_iso}")
        # Save reserve settings so we can calculate account reserve
        self.reserve_base = xrpl.utils.drops_to_xrp(str(message["reserve_base"]))
        self.reserve_inc = xrpl.utils.drops_to_xrp(str(message["reserve_inc"]))

    def calculate_reserve_xrp(self, owner_count):
        """
        Calculates how much XRP the user needs to reserve based on the account's
        OwnerCount and the reserve values in the latest ledger.
        """
        if self.reserve_base == None or self.reserve_inc == None:
            return None
        oc_decimal = Decimal(owner_count)
        reserve_xrp = self.reserve_base + (self.reserve_inc * oc_decimal)
        return reserve_xrp

    def update_account(self, acct):
        """
        Update the account info UI based on an account_info response.
        """
        xrp_balance = str(xrpl.utils.drops_to_xrp(acct["Balance"]))
        self.st_xrp_balance.SetLabel(xrp_balance)

        # Display account reserve.
        reserve_xrp = self.calculate_reserve_xrp(acct.get("OwnerCount", 0))
        if reserve_xrp != None:
            self.st_reserve.SetLabel(str(reserve_xrp))

    def displayable_amount(self, a):
        """
        Convert an arbitrary amount value from the XRPL to a string to be
        displayed to the user:
        - Convert drops of XRP to 6-decimal XRP (e.g. '12.345000 XRP')
        - For issued tokens, show amount, currency code, and issuer. For
          example, 100 USD issued by address r12345... is returned as
          '100 USD.r12345...'

        Leaves non-standard (hex) currency codes as-is.
        """
        if a == "unavailable":
            # Special case for pre-2014 partial payments.
            return a
        elif type(a) == str:
            # It's an XRP amount in drops. Convert to decimal.
            return f"{xrpl.utils.drops_to_xrp(a)} XRP"
        else:
            # It's a token amount.
            return f"{a['value']} {a['currency']}.{a['issuer']}"

    def add_tx_row(self, t, prepend=False):
        """
        Add one row to the account transaction history control. Helper function
        called by other methods.
        """
        conf_dt = xrpl.utils.ripple_time_to_datetime(t["tx"]["date"])
        # Convert datetime to locale-default representation & time zone
        confirmation_time = conf_dt.astimezone().strftime("%c")

        tx_hash = t["tx"]["hash"]
        tx_type = t["tx"]["TransactionType"]
        from_acct = t["tx"].get("Account") or ""
        if from_acct == self.classic_address:
            from_acct = "(Me)"
        to_acct = t["tx"].get("Destination") or ""
        if to_acct == self.classic_address:
            to_acct = "(Me)"

        delivered_amt = t["meta"].get("delivered_amount")
        if delivered_amt:
            delivered_amt = self.displayable_amount(delivered_amt)
        else:
            delivered_amt = ""

        cols = (confirmation_time, tx_type, from_acct, to_acct, delivered_amt,
                tx_hash, str(t))
        if prepend:
            self.tx_list.PrependItem(cols)
        else:
            self.tx_list.AppendItem(cols)

    def update_account_tx(self, data):
        """
        Update the transaction history tab with information from an account_tx
        response.
        """
        txs = data["transactions"]
        # Note: if you extend the code to do paginated responses, you might want
        # to keep previous history instead of deleting the contents first.
        self.tx_list.DeleteAllItems()
        for t in txs:
            self.add_tx_row(t)

    def add_tx_from_sub(self, t):
        """
        Add 1 transaction to the history based on a subscription stream message.
        Assumes only validated transaction streams (e.g. transactions, accounts)
        not proposed transaction streams.

        Also, send a notification to the user about it.
        """
        # Convert to same format as account_tx results
        t["tx"] = t["transaction"]

        self.add_tx_row(t, prepend=True)
        # Scroll to top of list.
        self.tx_list.EnsureVisible(self.tx_list.RowToItem(0))

        # Send a notification message (aka a "toast") about the transaction.
        # Note the transaction stream and account_tx include all transactions
        # that "affect" the account, no just ones directly from/to the account.
        # For example, if the account has issued tokens, it gets notified when
        # other users transfer those tokens among themselves.
        notif = wx.adv.NotificationMessage(title="New Transaction", message =
                f"New {t['tx']['TransactionType']} transaction confirmed!")
        notif.SetFlags(wx.ICON_INFORMATION)
        notif.Show()


if __name__ == "__main__":
    WS_URL = "wss://s.altnet.rippletest.net:51233" # Testnet
    app = wx.App()
    frame = TWaXLFrame(WS_URL, test_network=True)
    frame.Show()
    app.MainLoop()
