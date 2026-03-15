with Arthexis.ORM;

package Apps.Views.Functions.Views_Functions is
   --  SQL-callable function registrations for the Views component app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Register Views component helper views.
end Apps.Views.Functions.Views_Functions;
