with Arthexis.ORM;

package Apps.Preview.Functions.Preview_Functions is
   --  SQL-callable function registrations for the Preview app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Register preview helper views and seed initial preview routes.
end Apps.Preview.Functions.Preview_Functions;
